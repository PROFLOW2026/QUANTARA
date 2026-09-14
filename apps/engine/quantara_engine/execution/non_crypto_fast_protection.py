"""1-minute SL/TP + marks for open US equity and FX positions (Research + Live Sim)."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import text

from quantara_engine.broker.accounts import LIVE_SIM_VIRTUAL_PORTFOLIO_ID
from quantara_engine.broker.execution_bridge import execute_through_broker
from quantara_engine.domain.types import Direction
from quantara_engine.execution.cost_profile import execution_assumptions_for
from quantara_engine.execution.crypto_fast_protection import (
    FAST_FETCH_LOOKBACK_MINUTES,
    FAST_PROTECTION_IDEMPOTENCY_PREFIX,
    _fetch_since_for_instrument,
)
from quantara_engine.execution.crypto_mark_valuation import (
    FAST_EQUITY_DB_SYMBOLS,
    FAST_FX_DB_SYMBOLS,
    FAST_NON_CRYPTO_DB_SYMBOLS,
    apply_fast_1m_marks,
    is_fast_protection_equity,
    is_fast_protection_fx,
    latest_completed_1m_close,
    prune_fast_canonical_marks,
)
from quantara_engine.execution.equity_live_mark import store_equity_alpaca_fallback_mark
from quantara_engine.execution.exit_triggers import detect_exit_trigger
from quantara_engine.execution.fx_fast_credit_guard import (
    can_fetch_twelve_data_1m,
    can_run_fast_fx_fetch,
    fx_fast_budget_report,
    quota_mode,
)
from quantara_engine.execution.fx_protection_sources import (
    STORED_1M_FRESHNESS,
    any_position_near_stop,
    latest_mark_from_candles,
    mark_provider_attempted,
    mark_provider_success,
    plan_fx_protection_fetch,
    record_protection_source,
)
from quantara_engine.execution.paper_broker import PaperBrokerAdapter
from quantara_engine.execution.position_management import (
    MAX_CANDLES_PER_POSITION_PER_RUN,
    _management_candles,
    clear_position_management_cursor,
    process_position_management,
)
from quantara_engine.live_sim.close_authority import finalize_live_sim_position_close
from quantara_engine.live_sim.opportunity import live_sim_execution_idempotency_key
from quantara_engine.market_data.adapters.alpaca import AlpacaError, AlpacaMarketDataProvider
from quantara_engine.market_data.adapters.tiingo import TiingoError, TiingoMarketDataProvider
from quantara_engine.market_data.adapters.twelvedata import TwelveDataError, TwelveDataMarketDataProvider
from quantara_engine.market_data.credits import FetchPriority as TDFetchPriority
from quantara_engine.market_data.polling import FAST_PROTECTION_TIMEFRAME
from quantara_engine.market_data.provider_budgets import FetchPriority as AlpacaFetchPriority
from quantara_engine.market_data.provider_budgets import FetchPriority as TiingoFetchPriority
from quantara_engine.market_data.provider_budgets import can_request as provider_can_request
from quantara_engine.market_data.provider_cooldown import is_in_cooldown
from quantara_engine.market_data.registry import ProviderName, get_asset, provider_symbol
from quantara_engine.market_data.sessions import is_forex_session, is_us_equity_rth
from quantara_engine.market_data.symbols import normalize_db_symbol
from quantara_engine.persistence.batch_summary import batch_open_positions_by_portfolio

if TYPE_CHECKING:
    from quantara_engine.domain.types import Instrument, Position, StrategyInstance
    from quantara_engine.persistence.store import TradingStore

logger = logging.getLogger(__name__)

NON_CRYPTO_FAST_PROTECTION_CURSORS_KEY = "non_crypto_fast_protection_cursors"


def is_non_crypto_fast_protection(symbol: str) -> bool:
    return normalize_db_symbol(symbol) in FAST_NON_CRYPTO_DB_SYMBOLS


def _get_fast_cursors(store: TradingStore) -> dict[str, str]:
    return dict(store.get_settings_dict().get(NON_CRYPTO_FAST_PROTECTION_CURSORS_KEY) or {})


def _save_fast_cursors(store: TradingStore, cursors: dict[str, str]) -> None:
    store.update_settings(NON_CRYPTO_FAST_PROTECTION_CURSORS_KEY, cursors, flush=False)


def _symbol_filter(symbol: str) -> bool:
    return is_non_crypto_fast_protection(symbol)


def _collect_research_work(
    store: TradingStore,
) -> list[tuple[Position, StrategyInstance, Instrument]]:
    _, _, entries = store.list_all_competition_entries()
    instance_by_id = {entry["instance"].id: entry["instance"] for entry in entries}
    portfolio_ids = list({entry["portfolio"].id for entry in entries})
    open_by_portfolio = batch_open_positions_by_portfolio(store, portfolio_ids)

    instrument_cache: dict[str, Instrument | None] = {}
    work: list[tuple[Position, StrategyInstance, Instrument]] = []
    for positions in open_by_portfolio.values():
        for position in positions:
            instance = instance_by_id.get(position.strategy_instance_id)
            if not instance:
                continue
            if position.instrument_id not in instrument_cache:
                instrument_cache[position.instrument_id] = store.get_instrument_by_id(
                    position.instrument_id
                )
            instrument = instrument_cache.get(position.instrument_id)
            if not instrument or not _symbol_filter(instrument.symbol):
                continue
            work.append((position, instance, instrument))
    return work


def _collect_live_sim_rows(store: TradingStore) -> list[dict[str, Any]]:
    from quantara_engine.live_sim.execution_routing import query_open_live_sim_position_rows

    rows = query_open_live_sim_position_rows(store)
    return [row for row in rows if _symbol_filter(row["symbol"])]


def _symbols_with_open_positions(
    research_work: list[tuple[Position, StrategyInstance, Instrument]],
    live_sim_rows: list[dict[str, Any]],
) -> tuple[set[str], set[str], set[str]]:
    all_symbols: set[str] = set()
    equity: set[str] = set()
    fx: set[str] = set()
    for _, _, instrument in research_work:
        db = normalize_db_symbol(instrument.symbol)
        all_symbols.add(db)
        if is_fast_protection_equity(db):
            equity.add(db)
        elif is_fast_protection_fx(db):
            fx.add(db)
    for row in live_sim_rows:
        db = normalize_db_symbol(row["symbol"])
        all_symbols.add(db)
        if is_fast_protection_equity(db):
            equity.add(db)
        elif is_fast_protection_fx(db):
            fx.add(db)
    return all_symbols, equity, fx


def _fetch_alpaca_1m_batch(
    store: TradingStore,
    equity_symbols: set[str],
    *,
    since_by_instrument: dict[str, datetime],
    now: datetime,
) -> tuple[int, dict[str, list]]:
    if not equity_symbols or not is_us_equity_rth(now):
        return 0, {}

    entries: list[tuple[str, str]] = []
    since_floor = now - timedelta(minutes=FAST_FETCH_LOOKBACK_MINUTES)
    for db_sym in sorted(equity_symbols):
        instrument = store.get_instrument_by_symbol(db_sym)
        if not instrument:
            continue
        asset = get_asset(db_sym)
        if not asset:
            continue
        ticker = provider_symbol(asset, ProviderName.ALPACA)
        entries.append((instrument.id, ticker))

    if not entries:
        return 0, {}

    earliest_since = min(since_by_instrument.values()) if since_by_instrument else since_floor
    provider = AlpacaMarketDataProvider(
        store=store,
        caller="non_crypto_fast_protection",
        priority=AlpacaFetchPriority.OPEN_POSITION,
    )
    try:
        by_instrument = provider.fetch_equity_1m_batch(entries, since=earliest_since)
    except AlpacaError as exc:
        logger.warning("Alpaca 1m batch failed: %s", exc)
        return 0, {}

    candles_by_instrument: dict[str, list] = {}
    for iid, candles in by_instrument.items():
        for candle in candles:
            store.upsert_candle(candle)
        since = since_by_instrument.get(iid, earliest_since)
        stored = store.list_candles(
            iid,
            FAST_PROTECTION_TIMEFRAME,
            since=since,
            limit=MAX_CANDLES_PER_POSITION_PER_RUN,
        )
        candles_by_instrument[iid] = stored or candles
    return 1, candles_by_instrument


def _fetch_twelve_data_1m(
    store: TradingStore,
    db_sym: str,
    *,
    since: datetime,
) -> list:
    instrument = store.get_instrument_by_symbol(db_sym)
    if not instrument:
        return []
    asset = get_asset(db_sym)
    if not asset:
        return []
    provider = TwelveDataMarketDataProvider(
        store=store,
        caller="non_crypto_fast_protection",
        priority=TDFetchPriority.OPEN_POSITION,
        allow_non_canonical_timeframes=True,
        asset=asset,
    )
    try:
        candles = provider.fetch_latest(instrument.id, FAST_PROTECTION_TIMEFRAME, since=since)
    except TwelveDataError as exc:
        logger.warning("Twelve Data 1m fetch failed for %s: %s", db_sym, exc)
        return []
    for candle in candles:
        store.upsert_candle(candle)
    return candles


def _fetch_tiingo_1m(
    store: TradingStore,
    db_sym: str,
    *,
    since: datetime,
) -> list:
    instrument = store.get_instrument_by_symbol(db_sym)
    if not instrument:
        return []
    asset = get_asset(db_sym)
    if not asset:
        return []
    provider = TiingoMarketDataProvider(
        store=store,
        caller="non_crypto_fast_protection",
        priority=TiingoFetchPriority.OPEN_POSITION,
        asset=asset,
        allow_non_canonical_timeframes=True,
    )
    try:
        candles = provider.fetch_latest(instrument.id, FAST_PROTECTION_TIMEFRAME, since=since)
    except TiingoError as exc:
        logger.warning("Tiingo 1m fetch failed for %s: %s", db_sym, exc)
        return []
    for candle in candles:
        store.upsert_candle(candle)
    return candles


def _stored_1m_fresh(
    store: TradingStore,
    instrument_id: str,
    *,
    since: datetime,
    now: datetime,
) -> tuple[bool, list]:
    stored = store.list_candles(
        instrument_id,
        FAST_PROTECTION_TIMEFRAME,
        since=since,
        limit=MAX_CANDLES_PER_POSITION_PER_RUN,
    )
    return _stored_still_usable(stored, now), stored


def _stored_still_usable(stored: list, now: datetime) -> bool:
    from quantara_engine.market_data.polling import is_bar_complete

    completed = [
        c
        for c in stored
        if c.is_complete or is_bar_complete(c.timestamp, c.timeframe, now)
    ]
    if not completed:
        return False
    completed.sort(key=lambda c: c.timestamp)
    age = now - completed[-1].timestamp
    return age <= STORED_1M_FRESHNESS


def _fx_positions_for_symbol(
    research_work: list[tuple[Position, StrategyInstance, Instrument]],
    live_sim_rows: list[dict[str, Any]],
    db_sym: str,
) -> list[Position]:
    from quantara_engine.domain.types import Direction as D
    from quantara_engine.domain.types import Position as DomainPosition

    out: list[Position] = []
    for position, _, instrument in research_work:
        if normalize_db_symbol(instrument.symbol) == db_sym:
            out.append(position)
    for row in live_sim_rows:
        if normalize_db_symbol(row["symbol"]) != db_sym:
            continue
        direction = D.LONG if str(row["direction"]).lower() == "long" else D.SHORT
        out.append(
            DomainPosition(
                id=row["id"],
                portfolio_id=LIVE_SIM_VIRTUAL_PORTFOLIO_ID,
                strategy_instance_id="",
                instrument_id=row["instrument_id"],
                direction=direction,
                quantity=Decimal(str(row["quantity"])),
                entry_price=Decimal(str(row["entry_price"])),
                current_price=Decimal(str(row["entry_price"])),
                stop_loss=Decimal(str(row["stop_loss"])),
                take_profit=Decimal(str(row["take_profit"])) if row["take_profit"] else None,
                opened_at=datetime.now(timezone.utc),
            )
        )
    return out


def _fetch_fx_protection_candles(
    store: TradingStore,
    open_fx_symbols: set[str],
    *,
    research_work: list[tuple[Position, StrategyInstance, Instrument]],
    live_sim_rows: list[dict[str, Any]],
    since_by_instrument: dict[str, datetime],
    now: datetime,
) -> tuple[int, dict[str, list], dict[str, Any]]:
    """
    Per-symbol FX protection fetch (NOT per position).

    Hierarchy: stored completed 1m → Tiingo 1m → Twelve Data 1m → 5m fail-safe.
    """
    candles_by_instrument: dict[str, list] = {}
    fetches = 0
    fx_fetched: list[str] = []
    fx_sources: dict[str, str] = {}
    fx_skipped_credit: list[str] = []
    td_calls = 0
    tiingo_calls = 0

    tiingo_ok = (
        not is_in_cooldown(store, "tiingo")
        and provider_can_request(store, "tiingo", purpose="candles")
    )
    mode = quota_mode(store, tiingo_primary_ok=tiingo_ok)
    td_ok = can_fetch_twelve_data_1m(store)

    # Always evaluate EVERY open FX symbol once (shared dataset for all positions).
    for db_sym in sorted(open_fx_symbols):
        instrument = store.get_instrument_by_symbol(db_sym)
        if not instrument:
            continue
        since = since_by_instrument.get(instrument.id, now - timedelta(minutes=10))
        fresh, stored = _stored_1m_fresh(store, instrument.id, since=since, now=now)
        positions = _fx_positions_for_symbol(research_work, live_sim_rows, db_sym)
        mark = latest_mark_from_candles(stored, now) if stored else None
        near = any_position_near_stop(positions, mark)

        plan = plan_fx_protection_fetch(
            store,
            db_sym,
            quota_mode=mode,
            tiingo_eligible=tiingo_ok,
            td_eligible=td_ok,
            near_sl=near,
            has_fresh_stored_1m=fresh,
            now=now,
        )

        fetched: list = []
        used_provider: str | None = None
        if plan.fetch_provider == "tiingo":
            fetched = _fetch_tiingo_1m(store, db_sym, since=since)
            fetches += 1
            tiingo_calls += 1
            # Always advance attempt cadence — empty/429 must not retry every minute.
            mark_provider_attempted(store, db_sym, "tiingo", now=now)
            if fetched:
                mark_provider_success(store, db_sym, "tiingo", now=now)
                tiingo_ok = True
                used_provider = "tiingo"
            else:
                tiingo_ok = False
                # Fresh stored 1m: NEVER same-cycle TD. Protect from stored until next Tiingo window.
                if not fresh:
                    mode = quota_mode(store, tiingo_primary_ok=False)
                    plan = plan_fx_protection_fetch(
                        store,
                        db_sym,
                        quota_mode=mode,
                        tiingo_eligible=False,
                        td_eligible=td_ok,
                        near_sl=near,
                        has_fresh_stored_1m=False,
                        now=now,
                    )
                    if plan.fetch_provider == "twelvedata":
                        fetched = _fetch_twelve_data_1m(store, db_sym, since=since)
                        fetches += 1
                        td_calls += 1
                        mark_provider_attempted(store, db_sym, "twelvedata", now=now)
                        if fetched:
                            mark_provider_success(store, db_sym, "twelvedata", now=now)
                            used_provider = "twelvedata"
        elif plan.fetch_provider == "twelvedata":
            fetched = _fetch_twelve_data_1m(store, db_sym, since=since)
            fetches += 1
            td_calls += 1
            mark_provider_attempted(store, db_sym, "twelvedata", now=now)
            if fetched:
                mark_provider_success(store, db_sym, "twelvedata", now=now)
                used_provider = "twelvedata"

        stored_after = store.list_candles(
            instrument.id,
            FAST_PROTECTION_TIMEFRAME,
            since=since,
            limit=MAX_CANDLES_PER_POSITION_PER_RUN,
        )
        candles_by_instrument[instrument.id] = stored_after or fetched or stored

        if used_provider == "tiingo":
            source = "tiingo_1m"
        elif used_provider == "twelvedata":
            source = "twelve_data_1m"
        elif fresh or (stored_after and _stored_still_usable(stored_after, now)):
            source = "stored_1m"
        else:
            source = plan.source if plan.source != "twelve_data_1m" else "5m_fallback"
        record_protection_source(store, db_sym, source, reason=plan.reason)
        fx_sources[db_sym] = source
        if used_provider:
            fx_fetched.append(db_sym)
        elif source == "5m_fallback":
            fx_skipped_credit.append(f"{db_sym}:{plan.reason}")

    meta = {
        "fx_fetched": fx_fetched,
        "fx_sources": fx_sources,
        "fx_skipped_credit": fx_skipped_credit,
        "td_calls": td_calls,
        "tiingo_calls": tiingo_calls,
        "quota_mode": mode,
    }
    return fetches, candles_by_instrument, meta


def _process_research(
    store: TradingStore,
    work: list[tuple[Position, StrategyInstance, Instrument]],
    *,
    now: datetime,
    cursors: dict[str, str],
    candles_by_instrument: dict[str, list],
) -> dict[str, int]:
    from quantara_engine.portfolio.currency import build_currency_context

    closed = 0
    checked = 0
    instruments = [inst for _, _, inst in work if inst]
    currency = build_currency_context(store, instruments) if instruments else None

    for position, instance, instrument in work:
        checked += 1
        if store.trade_exists_for_position(position.id):
            cursors.pop(position.id, None)
            continue

        last_raw = cursors.get(position.id)
        last_managed = (
            datetime.fromisoformat(last_raw.replace("Z", "+00:00")) if last_raw else None
        )
        prefetched = candles_by_instrument.get(instrument.id)
        if not _management_candles(
            store,
            position,
            instrument,
            FAST_PROTECTION_TIMEFRAME,
            last_managed=last_managed,
            now=now,
            prefetched=prefetched,
        ):
            continue

        result = process_position_management(
            store,
            position=position,
            instance=instance,
            instrument=instrument,
            now=now,
            cursors=cursors,
            prefetched=prefetched,
            currency=currency,
            monitor_timeframe=FAST_PROTECTION_TIMEFRAME,
            execution_timeframe=FAST_PROTECTION_TIMEFRAME,
            idempotency_prefix=FAST_PROTECTION_IDEMPOTENCY_PREFIX,
        )
        if result.get("status") == "closed":
            clear_position_management_cursor(store, position.id, flush=False)
            closed += 1

    return {"checked": checked, "closed": closed}


def _process_live_sim(
    store: TradingStore,
    rows: list[dict[str, Any]],
    *,
    now: datetime,
    cursors: dict[str, str],
    candles_by_instrument: dict[str, list],
) -> dict[str, int]:
    from quantara_engine.domain.types import Direction as D
    from quantara_engine.domain.types import Position as DomainPosition

    closed = 0
    for row in rows:
        pos_id = row["id"]
        account_slug = str(row["broker_account_slug"])
        instrument = store.get_instrument_by_id(row["instrument_id"])
        if not instrument:
            continue

        last_raw = cursors.get(pos_id)
        last_managed = (
            datetime.fromisoformat(last_raw.replace("Z", "+00:00")) if last_raw else None
        )
        direction = D.LONG if str(row["direction"]).lower() == "long" else D.SHORT
        pos = DomainPosition(
            id=pos_id,
            portfolio_id=LIVE_SIM_VIRTUAL_PORTFOLIO_ID,
            strategy_instance_id="",
            instrument_id=row["instrument_id"],
            direction=direction,
            quantity=Decimal(str(row["quantity"])),
            entry_price=Decimal(str(row["entry_price"])),
            current_price=Decimal(str(row["entry_price"])),
            stop_loss=Decimal(str(row["stop_loss"])),
            take_profit=Decimal(str(row["take_profit"])) if row["take_profit"] else None,
            opened_at=now,
        )

        prefetched = candles_by_instrument.get(instrument.id)
        pending = _management_candles(
            store,
            pos,
            instrument,
            FAST_PROTECTION_TIMEFRAME,
            last_managed=last_managed,
            now=now,
            prefetched=prefetched,
        )
        if not pending:
            continue

        for candle in pending:
            trigger = detect_exit_trigger(pos, candle)
            if not trigger:
                cursors[pos_id] = candle.timestamp.isoformat()
                continue

            reason, trigger_price = trigger
            assumptions = execution_assumptions_for(instrument, candle.close)
            broker = PaperBrokerAdapter(instrument.id, assumptions)
            close_dir = Direction.SHORT if direction == D.LONG else Direction.LONG
            _, fill = broker.execute_exit_at_trigger(
                direction,
                pos.quantity,
                candle,
                trigger_price,
                LIVE_SIM_VIRTUAL_PORTFOLIO_ID,
            )
            purpose = "sl" if reason.value == "sl" else "tp"
            idem = live_sim_execution_idempotency_key(
                account_slug,
                f"exit1m:{pos_id}:{candle.timestamp.isoformat()}:{purpose}",
            )
            broker_res = execute_through_broker(
                store,
                portfolio_id=LIVE_SIM_VIRTUAL_PORTFOLIO_ID,
                instrument=instrument,
                direction=close_dir,
                quantity=pos.quantity,
                fill=fill,
                execution_at=candle.timestamp,
                timeframe=FAST_PROTECTION_TIMEFRAME,
                idempotency_key=idem,
                is_close=True,
                strategy_position_id=pos_id,
                opportunity_key=row.get("opportunity_key"),
                order_purpose=purpose,
                skip_if_not_competition=False,
                account_slug=account_slug,
            )
            if finalize_live_sim_position_close(
                store,
                position_id=pos_id,
                closed_at=candle.timestamp,
                account_slug=account_slug,
                instrument_symbol=instrument.symbol,
                mark_price=candle.close,
                broker_res=broker_res,
                requested_quantity=pos.quantity,
            ):
                cursors.pop(pos_id, None)
                closed += 1
                logger.info("Live-sim 1m closed %s via %s", instrument.symbol, purpose)
            else:
                cursors[pos_id] = candle.timestamp.isoformat()
            break

    return {"checked": len(rows), "closed": closed}


def run_non_crypto_fast_protection(store: TradingStore, now: datetime) -> dict[str, Any]:
    """Fetch 1m equity/FX candles and run SL/TP when open positions exist."""
    from quantara_engine.trading.trading_controls import (
        allows_position_management,
        load_trading_control,
    )

    settings = store.get_settings_dict()
    if not settings.get("paper_trading_enabled", True):
        return {"status": "skipped", "reason": "paper_trading_disabled", "fetches": 0}

    control = load_trading_control(settings)
    if not allows_position_management(control):
        return {"status": "skipped", "reason": "trading_control", "fetches": 0}

    research_work = _collect_research_work(store)
    live_sim_rows = _collect_live_sim_rows(store)
    open_symbols, _, open_fx_symbols = _symbols_with_open_positions(
        research_work, live_sim_rows
    )
    mark_equity_symbols = (
        set(FAST_EQUITY_DB_SYMBOLS) if is_us_equity_rth(now) else set()
    )
    mark_symbols = mark_equity_symbols | open_symbols

    if not mark_symbols and not research_work and not live_sim_rows:
        return {
            "status": "skipped",
            "reason": "no_mark_or_protection_work",
            "fetches": 0,
            "research_checked": 0,
            "live_sim_checked": 0,
        }

    cursors = _get_fast_cursors(store)
    position_ids_by_instrument: dict[str, list[str]] = {}
    for position, _, instrument in research_work:
        position_ids_by_instrument.setdefault(instrument.id, []).append(position.id)
    for row in live_sim_rows:
        position_ids_by_instrument.setdefault(row["instrument_id"], []).append(row["id"])

    since_by_instrument: dict[str, datetime] = {}
    for db_sym in mark_symbols:
        instrument = store.get_instrument_by_symbol(db_sym)
        if not instrument:
            continue
        pids = position_ids_by_instrument.get(instrument.id, [])
        if pids:
            since_by_instrument[instrument.id] = _fetch_since_for_instrument(
                store,
                instrument.id,
                pids,
                cursors,
                now,
            )
        else:
            since_by_instrument[instrument.id] = now - timedelta(
                minutes=FAST_FETCH_LOOKBACK_MINUTES
            )

    fetches = 0
    candles_by_instrument: dict[str, list] = {}
    alpaca_batches = 0
    fx_fetched: list[str] = []
    fx_skipped_credit: list[str] = []
    fx_skipped_session: list[str] = []
    fx_sources: dict[str, str] = {}
    td_calls = 0
    tiingo_calls = 0
    protection_mode = ""

    if mark_equity_symbols:
        if is_us_equity_rth(now):
            alpaca_batches, equity_candles = _fetch_alpaca_1m_batch(
                store,
                mark_equity_symbols,
                since_by_instrument=since_by_instrument,
                now=now,
            )
            fetches += alpaca_batches
            candles_by_instrument.update(equity_candles)
        else:
            logger.debug("Skipping equity 1m — outside US RTH")

    if open_fx_symbols and is_forex_session(now):
        if can_run_fast_fx_fetch(store):
            fx_fetches, fx_candles, fx_meta = _fetch_fx_protection_candles(
                store,
                open_fx_symbols,
                research_work=research_work,
                live_sim_rows=live_sim_rows,
                since_by_instrument=since_by_instrument,
                now=now,
            )
            fetches += fx_fetches
            candles_by_instrument.update(fx_candles)
            fx_fetched = list(fx_meta.get("fx_fetched") or [])
            fx_skipped_credit = list(fx_meta.get("fx_skipped_credit") or [])
            fx_sources = dict(fx_meta.get("fx_sources") or {})
            td_calls = int(fx_meta.get("td_calls") or 0)
            tiingo_calls = int(fx_meta.get("tiingo_calls") or 0)
            protection_mode = str(fx_meta.get("quota_mode") or "")
        else:
            fx_skipped_credit.extend(sorted(open_fx_symbols))
            for db_sym in sorted(open_fx_symbols):
                record_protection_source(store, db_sym, "5m_fallback", reason="fast_fx_exhausted")
                fx_sources[db_sym] = "5m_fallback"
            protection_mode = "EXHAUSTED"
    elif open_fx_symbols:
        fx_skipped_session.extend(sorted(open_fx_symbols))

    research_result = _process_research(
        store,
        research_work,
        now=now,
        cursors=cursors,
        candles_by_instrument=candles_by_instrument,
    )
    live_sim_result = _process_live_sim(
        store,
        live_sim_rows,
        now=now,
        cursors=cursors,
        candles_by_instrument=candles_by_instrument,
    )
    _save_fast_cursors(store, cursors)

    prune_fast_canonical_marks(store, mark_symbols)
    marks_to_apply: dict[str, tuple[Decimal, datetime]] = {}
    for db_sym in mark_symbols:
        instrument = store.get_instrument_by_symbol(db_sym)
        if not instrument:
            continue
        candles = candles_by_instrument.get(instrument.id) or []
        latest = latest_completed_1m_close(candles, now)
        if latest:
            marks_to_apply[db_sym] = latest

    applied_symbols: list[str] = []
    if marks_to_apply:
        fx_marks: dict[str, tuple[Decimal, datetime]] = {}
        for db_sym, mark in marks_to_apply.items():
            if is_fast_protection_equity(db_sym):
                store_equity_alpaca_fallback_mark(
                    store,
                    db_sym,
                    mark[0],
                    mark[1],
                )
                applied_symbols.append(db_sym)
            else:
                fx_marks[db_sym] = mark
        if fx_marks:
            fx_report = apply_fast_1m_marks(store, fx_marks, flush=False)
            applied_symbols.extend(fx_report.get("applied_symbols") or [])
    mark_report = {"applied_symbols": applied_symbols}

    return {
        "status": "success",
        "fetches": fetches,
        "alpaca_batches": alpaca_batches,
        "symbols": sorted(mark_symbols),
        "open_symbols": sorted(open_symbols),
        "equity_mark_symbols": sorted(mark_equity_symbols),
        "fx_symbols": sorted(open_fx_symbols),
        "fx_fetched": fx_fetched,
        "fx_sources": fx_sources,
        "fx_skipped_credit": fx_skipped_credit,
        "fx_skipped_session": fx_skipped_session,
        "fx_td_calls": td_calls,
        "fx_tiingo_calls": tiingo_calls,
        "protection_mode": protection_mode or fx_fast_budget_report(store).get("quota_mode"),
        "fx_budget": fx_fast_budget_report(store),
        "research": research_result,
        "live_sim": live_sim_result,
        "marks_applied": mark_report.get("applied_symbols", []),
    }
