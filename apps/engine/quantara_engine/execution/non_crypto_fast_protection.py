"""1-minute SL/TP + marks for open US equity and FX positions (Research + Live Sim)."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import text

from quantara_engine.broker.accounts import LIVE_SIM_10K_ACCOUNT_SLUG, LIVE_SIM_VIRTUAL_PORTFOLIO_ID
from quantara_engine.broker.execution_bridge import execute_through_broker
from quantara_engine.broker.execution_service import BrokerExecutionService
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
from quantara_engine.execution.exit_triggers import detect_exit_trigger
from quantara_engine.execution.fx_fast_credit_guard import (
    can_run_fast_fx_fetch,
    fx_fast_budget_report,
    select_fx_symbols_this_cycle,
)
from quantara_engine.execution.paper_broker import PaperBrokerAdapter
from quantara_engine.execution.position_management import (
    MAX_CANDLES_PER_POSITION_PER_RUN,
    _management_candles,
    clear_position_management_cursor,
    process_position_management,
)
from quantara_engine.live_sim.opportunity import live_sim_execution_idempotency_key
from quantara_engine.market_data.adapters.alpaca import AlpacaError, AlpacaMarketDataProvider
from quantara_engine.market_data.adapters.twelvedata import TwelveDataError, TwelveDataMarketDataProvider
from quantara_engine.market_data.credits import FetchPriority as TDFetchPriority
from quantara_engine.market_data.polling import FAST_PROTECTION_TIMEFRAME
from quantara_engine.market_data.provider_budgets import FetchPriority as AlpacaFetchPriority
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
    account = store.session.execute(
        text("SELECT id::text FROM broker_accounts WHERE slug = :slug"),
        {"slug": LIVE_SIM_10K_ACCOUNT_SLUG},
    ).mappings().first()
    if not account:
        return []

    rows = store.session.execute(
        text(
            """
            SELECT p.id::text, p.instrument_id::text, p.direction::text, p.quantity,
                   p.entry_price, p.stop_loss, p.take_profit, p.timeframe,
                   p.canonical_opportunity_key, i.symbol
            FROM live_sim_positions p
            JOIN instruments i ON i.id = p.instrument_id
            WHERE p.broker_account_id = :aid AND p.status = 'open'
            """
        ),
        {"aid": account["id"]},
    ).mappings().all()
    return [dict(row) for row in rows if _symbol_filter(row["symbol"])]


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
                LIVE_SIM_10K_ACCOUNT_SLUG,
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
                order_purpose=purpose,
                skip_if_not_competition=False,
                account_slug=LIVE_SIM_10K_ACCOUNT_SLUG,
            )
            if broker_res and broker_res.accepted:
                store.session.execute(
                    text(
                        """
                        UPDATE live_sim_positions
                        SET status = 'closed', closed_at = :ts, updated_at = NOW()
                        WHERE id = :id AND status = 'open'
                        """
                    ),
                    {"id": pos_id, "ts": candle.timestamp},
                )
                svc = BrokerExecutionService(store, account_slug=LIVE_SIM_10K_ACCOUNT_SLUG)
                svc.mark_to_market({instrument.symbol.upper(): candle.close}, at=candle.timestamp)
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
    all_symbols, equity_symbols, fx_symbols = _symbols_with_open_positions(
        research_work, live_sim_rows
    )

    if not all_symbols:
        return {
            "status": "skipped",
            "reason": "no_open_non_crypto_positions",
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
    for db_sym in all_symbols:
        instrument = store.get_instrument_by_symbol(db_sym)
        if not instrument:
            continue
        since_by_instrument[instrument.id] = _fetch_since_for_instrument(
            store,
            instrument.id,
            position_ids_by_instrument.get(instrument.id, []),
            cursors,
            now,
        )

    fetches = 0
    candles_by_instrument: dict[str, list] = {}
    alpaca_batches = 0
    fx_fetched: list[str] = []
    fx_skipped_credit: list[str] = []
    fx_skipped_session: list[str] = []

    if equity_symbols:
        if is_us_equity_rth(now):
            alpaca_batches, equity_candles = _fetch_alpaca_1m_batch(
                store,
                equity_symbols,
                since_by_instrument=since_by_instrument,
                now=now,
            )
            fetches += alpaca_batches
            candles_by_instrument.update(equity_candles)
        else:
            logger.debug("Skipping equity 1m — outside US RTH")

    if fx_symbols and is_forex_session(now):
        if can_run_fast_fx_fetch(store):
            selected = select_fx_symbols_this_cycle(sorted(fx_symbols), now=now)
            for db_sym in selected:
                instrument = store.get_instrument_by_symbol(db_sym)
                if not instrument:
                    continue
                since = since_by_instrument.get(instrument.id, now - timedelta(minutes=10))
                fetched = _fetch_twelve_data_1m(store, db_sym, since=since)
                fetches += 1 if fetched is not None else 0
                stored = store.list_candles(
                    instrument.id,
                    FAST_PROTECTION_TIMEFRAME,
                    since=since,
                    limit=MAX_CANDLES_PER_POSITION_PER_RUN,
                )
                candles_by_instrument[instrument.id] = stored or fetched
                fx_fetched.append(db_sym)
            not_selected = sorted(set(fx_symbols) - set(selected))
            for sym in not_selected:
                fx_skipped_credit.append(f"{sym}:budget_alternate")
        else:
            fx_skipped_credit.extend(sorted(fx_symbols))
    elif fx_symbols:
        fx_skipped_session.extend(sorted(fx_symbols))

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

    prune_fast_canonical_marks(store, all_symbols)
    marks_to_apply: dict[str, tuple[Decimal, datetime]] = {}
    for db_sym in all_symbols:
        instrument = store.get_instrument_by_symbol(db_sym)
        if not instrument:
            continue
        candles = candles_by_instrument.get(instrument.id) or []
        latest = latest_completed_1m_close(candles, now)
        if latest:
            marks_to_apply[db_sym] = latest

    mark_report = (
        apply_fast_1m_marks(store, marks_to_apply, flush=False)
        if marks_to_apply
        else {"applied_symbols": []}
    )

    return {
        "status": "success",
        "fetches": fetches,
        "alpaca_batches": alpaca_batches,
        "symbols": sorted(all_symbols),
        "equity_symbols": sorted(equity_symbols),
        "fx_symbols": sorted(fx_symbols),
        "fx_fetched": fx_fetched,
        "fx_skipped_credit": fx_skipped_credit,
        "fx_skipped_session": fx_skipped_session,
        "fx_budget": fx_fast_budget_report(store),
        "research": research_result,
        "live_sim": live_sim_result,
        "marks_applied": mark_report.get("applied_symbols", []),
    }
