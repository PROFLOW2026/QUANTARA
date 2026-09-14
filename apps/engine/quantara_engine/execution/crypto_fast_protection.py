"""1-minute SL/TP protection for open BTC/ETH positions (Research + Live Sim).

Primary protection uses completed 1m OHLC. When the 1m feed is stale, completed
5m OHLC is used as a fail-safe for exits only (never for strategy entries).
"""

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
from quantara_engine.execution.exit_triggers import detect_exit_trigger
from quantara_engine.execution.paper_broker import PaperBrokerAdapter
from quantara_engine.execution.position_management import (
    MAX_CANDLES_PER_POSITION_PER_RUN,
    _management_candles,
    clear_position_management_cursor,
    process_position_management,
)
from quantara_engine.live_sim.close_authority import finalize_live_sim_position_close
from quantara_engine.live_sim.opportunity import live_sim_execution_idempotency_key
from quantara_engine.market_data.adapters.coinbase import CoinbaseMarketDataProvider, CoinbaseError
from quantara_engine.market_data.polling import FAST_PROTECTION_TIMEFRAME, is_bar_complete
from quantara_engine.market_data.provider_budgets import FetchPriority
from quantara_engine.market_data.registry import get_asset
from quantara_engine.market_data.symbols import normalize_db_symbol
from quantara_engine.persistence.batch_summary import batch_open_positions_by_portfolio

if TYPE_CHECKING:
    from quantara_engine.domain.types import Instrument, Position, StrategyInstance
    from quantara_engine.persistence.store import TradingStore

logger = logging.getLogger(__name__)

from quantara_engine.execution.crypto_mark_valuation import (
    FAST_CRYPTO_DB_SYMBOLS,
    is_fast_protection_crypto,
)

CONTINUOUS_MARK_CRYPTO_SYMBOLS = FAST_CRYPTO_DB_SYMBOLS

CRYPTO_FAST_PROTECTION_CURSORS_KEY = "crypto_fast_protection_cursors"
CRYPTO_5M_FALLBACK_CURSORS_KEY = "crypto_5m_fallback_protection_cursors"
FAST_PROTECTION_IDEMPOTENCY_PREFIX = "pm1m"
FAST_PROTECTION_5M_IDEMPOTENCY_PREFIX = "pm5m_fb"
FAST_FETCH_LOOKBACK_MINUTES = 10
# 1m job runs every minute; >3 completed minutes without a durable bar = stale.
CRYPTO_1M_STALE_MINUTES = 3
FALLBACK_TIMEFRAME = "5m"


def _get_fast_cursors(store: TradingStore) -> dict[str, str]:
    return dict(store.get_settings_dict().get(CRYPTO_FAST_PROTECTION_CURSORS_KEY) or {})


def _save_fast_cursors(store: TradingStore, cursors: dict[str, str]) -> None:
    store.update_settings(CRYPTO_FAST_PROTECTION_CURSORS_KEY, cursors, flush=False)


def _get_5m_fallback_cursors(store: TradingStore) -> dict[str, str]:
    return dict(store.get_settings_dict().get(CRYPTO_5M_FALLBACK_CURSORS_KEY) or {})


def _save_5m_fallback_cursors(store: TradingStore, cursors: dict[str, str]) -> None:
    store.update_settings(CRYPTO_5M_FALLBACK_CURSORS_KEY, cursors, flush=False)


def _as_utc(ts: datetime) -> datetime:
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


def latest_completed_candle_ts(
    candles: list,
    timeframe: str,
    now: datetime,
) -> datetime | None:
    latest: datetime | None = None
    for candle in candles:
        ts = _as_utc(candle.timestamp)
        if not is_bar_complete(ts, timeframe, now):
            continue
        if latest is None or ts > latest:
            latest = ts
    return latest


def crypto_1m_age_minutes(
    latest_1m: datetime | None,
    now: datetime,
) -> float | None:
    if latest_1m is None:
        return None
    return max(0.0, (_as_utc(now) - _as_utc(latest_1m)).total_seconds() / 60.0)


def is_crypto_1m_stale(latest_1m: datetime | None, now: datetime) -> bool:
    age = crypto_1m_age_minutes(latest_1m, now)
    if age is None:
        return True
    return age > CRYPTO_1M_STALE_MINUTES


def _collect_research_crypto_work(
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
            if not instrument or not is_fast_protection_crypto(instrument.symbol):
                continue
            work.append((position, instance, instrument))
    return work


def _collect_live_sim_crypto_rows(store: TradingStore) -> list[dict[str, Any]]:
    from quantara_engine.live_sim.execution_routing import query_open_live_sim_position_rows

    rows = query_open_live_sim_position_rows(store)
    return [row for row in rows if is_fast_protection_crypto(row["symbol"])]


def _symbols_with_open_crypto(
    research_work: list[tuple[Position, StrategyInstance, Instrument]],
    live_sim_rows: list[dict[str, Any]],
) -> set[str]:
    symbols: set[str] = set()
    for _, _, instrument in research_work:
        symbols.add(normalize_db_symbol(instrument.symbol))
    for row in live_sim_rows:
        symbols.add(normalize_db_symbol(row["symbol"]))
    return symbols


def _fetch_since_for_instrument(
    store: TradingStore,
    instrument_id: str,
    position_ids: list[str],
    cursors: dict[str, str],
    now: datetime,
) -> datetime:
    floors: list[datetime] = []
    for pid in position_ids:
        raw = cursors.get(pid)
        if raw:
            floors.append(datetime.fromisoformat(raw.replace("Z", "+00:00")))
    latest_stored = store.latest_candle_timestamp(instrument_id, FAST_PROTECTION_TIMEFRAME)
    if latest_stored is not None:
        if latest_stored.tzinfo is None:
            latest_stored = latest_stored.replace(tzinfo=timezone.utc)
        floors.append(latest_stored)
    # On recovery with open positions, also allow backfill from oldest open.
    if position_ids and floors:
        since = min(floors) - timedelta(minutes=1)
    elif floors:
        since = min(floors) - timedelta(minutes=1)
    else:
        since = now - timedelta(minutes=FAST_FETCH_LOOKBACK_MINUTES)
    # Cap lookback to avoid unbounded provider calls, but allow multi-hour recovery
    # across cycles via cursors advancing.
    earliest = now - timedelta(hours=6)
    if since < earliest:
        since = earliest
    return since


def _fetch_and_store_1m(
    store: TradingStore,
    instrument: Instrument,
    *,
    since: datetime,
) -> list:
    asset = get_asset(normalize_db_symbol(instrument.symbol))
    if not asset:
        return []
    provider = CoinbaseMarketDataProvider(
        store=store,
        caller="crypto_fast_protection",
        priority=FetchPriority.OPEN_POSITION,
        asset=asset,
    )
    try:
        candles = provider.fetch_latest(instrument.id, FAST_PROTECTION_TIMEFRAME, since=since)
    except CoinbaseError as exc:
        logger.warning("1m fetch failed for %s: %s", instrument.symbol, exc)
        return []

    for candle in candles:
        store.upsert_candle(candle)
    return candles


def _commit_market_data(store: TradingStore) -> None:
    """Make fetched candles durable before position-close work can fail."""
    store.session.flush()
    store.session.commit()


def _protect_one_research_position(
    store: TradingStore,
    position: Position,
    instance: StrategyInstance,
    instrument: Instrument,
    *,
    now: datetime,
    cursors: dict[str, str],
    fallback_cursors: dict[str, str],
    candles_1m: list,
    candles_5m: list,
    use_5m_fallback: bool,
    currency,
) -> dict[str, Any]:
    if store.trade_exists_for_position(position.id):
        cursors.pop(position.id, None)
        fallback_cursors.pop(position.id, None)
        return {"position_id": position.id, "status": "already_closed"}

    source = FALLBACK_TIMEFRAME if use_5m_fallback else FAST_PROTECTION_TIMEFRAME
    monitor_candles = candles_5m if use_5m_fallback else candles_1m
    active_cursors = fallback_cursors if use_5m_fallback else cursors
    idem_prefix = (
        FAST_PROTECTION_5M_IDEMPOTENCY_PREFIX
        if use_5m_fallback
        else FAST_PROTECTION_IDEMPOTENCY_PREFIX
    )

    last_raw = active_cursors.get(position.id)
    last_managed = (
        datetime.fromisoformat(last_raw.replace("Z", "+00:00")) if last_raw else None
    )
    pending = _management_candles(
        store,
        position,
        instrument,
        source,
        last_managed=last_managed,
        now=now,
        prefetched=monitor_candles,
    )
    if not pending:
        return {
            "position_id": position.id,
            "status": "no_pending_candles",
            "protection_source": source,
        }

    result = process_position_management(
        store,
        position=position,
        instance=instance,
        instrument=instrument,
        now=now,
        cursors=active_cursors,
        prefetched=monitor_candles,
        currency=currency,
        monitor_timeframe=source,
        execution_timeframe=source,
        idempotency_prefix=idem_prefix,
    )
    if result.get("status") == "closed":
        clear_position_management_cursor(store, position.id, flush=False)
        cursors.pop(position.id, None)
        fallback_cursors.pop(position.id, None)
        meta = dict(result)
        meta["protection_source"] = source
        if use_5m_fallback:
            meta["fallback"] = True
        return meta
    result["protection_source"] = source
    return result


def _process_research_crypto(
    store: TradingStore,
    work: list[tuple[Position, StrategyInstance, Instrument]],
    *,
    now: datetime,
    cursors: dict[str, str],
    fallback_cursors: dict[str, str],
    candles_1m_by_instrument: dict[str, list],
    candles_5m_by_instrument: dict[str, list],
    stale_by_instrument: dict[str, bool],
) -> dict[str, Any]:
    from quantara_engine.portfolio.currency import build_currency_context

    closed = 0
    checked = 0
    errors: list[dict[str, Any]] = []
    sources: dict[str, int] = {"1m": 0, "5m_fallback": 0}
    instruments = [inst for _, _, inst in work if inst]
    currency = build_currency_context(store, instruments) if instruments else None

    for position, instance, instrument in work:
        checked += 1
        use_fallback = bool(stale_by_instrument.get(instrument.id))
        nested = store.session.begin_nested()
        try:
            result = _protect_one_research_position(
                store,
                position,
                instance,
                instrument,
                now=now,
                cursors=cursors,
                fallback_cursors=fallback_cursors,
                candles_1m=candles_1m_by_instrument.get(instrument.id) or [],
                candles_5m=candles_5m_by_instrument.get(instrument.id) or [],
                use_5m_fallback=use_fallback,
                currency=currency,
            )
            nested.commit()
            if result.get("status") == "closed":
                closed += 1
            src = result.get("protection_source")
            if src == FAST_PROTECTION_TIMEFRAME:
                sources["1m"] += 1
            elif src == FALLBACK_TIMEFRAME:
                sources["5m_fallback"] += 1
        except Exception as exc:
            nested.rollback()
            logger.exception(
                "Crypto protection failed for research position %s (%s)",
                position.id,
                getattr(instrument, "symbol", "?"),
            )
            errors.append(
                {
                    "position_id": position.id,
                    "symbol": getattr(instrument, "symbol", None),
                    "error": str(exc),
                }
            )

    return {
        "checked": checked,
        "closed": closed,
        "errors": errors,
        "sources": sources,
    }


def _process_live_sim_crypto(
    store: TradingStore,
    rows: list[dict[str, Any]],
    *,
    now: datetime,
    cursors: dict[str, str],
    fallback_cursors: dict[str, str],
    candles_1m_by_instrument: dict[str, list],
    candles_5m_by_instrument: dict[str, list],
    stale_by_instrument: dict[str, bool],
) -> dict[str, Any]:
    from quantara_engine.domain.types import Direction as D
    from quantara_engine.domain.types import Position as DomainPosition

    closed = 0
    errors: list[dict[str, Any]] = []
    for row in rows:
        pos_id = row["id"]
        account_slug = str(row["broker_account_slug"])
        instrument = store.get_instrument_by_id(row["instrument_id"])
        if not instrument:
            continue

        use_fallback = bool(stale_by_instrument.get(instrument.id))
        source = FALLBACK_TIMEFRAME if use_fallback else FAST_PROTECTION_TIMEFRAME
        active_cursors = fallback_cursors if use_fallback else cursors
        prefetched = (
            candles_5m_by_instrument.get(instrument.id)
            if use_fallback
            else candles_1m_by_instrument.get(instrument.id)
        ) or []

        last_raw = active_cursors.get(pos_id)
        last_managed = (
            datetime.fromisoformat(last_raw.replace("Z", "+00:00")) if last_raw else None
        )
        direction = D.LONG if str(row["direction"]).lower() == "long" else D.SHORT
        opened_at = row.get("opened_at") or now
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
            opened_at=opened_at,
        )

        nested = store.session.begin_nested()
        try:
            pending = _management_candles(
                store,
                pos,
                instrument,
                source,
                last_managed=last_managed,
                now=now,
                prefetched=prefetched,
            )
            if not pending:
                nested.commit()
                continue

            for candle in pending:
                trigger = detect_exit_trigger(pos, candle)
                if not trigger:
                    active_cursors[pos_id] = candle.timestamp.isoformat()
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
                prefix = (
                    FAST_PROTECTION_5M_IDEMPOTENCY_PREFIX
                    if use_fallback
                    else FAST_PROTECTION_IDEMPOTENCY_PREFIX
                )
                idem = live_sim_execution_idempotency_key(
                    account_slug,
                    f"exit{prefix}:{pos_id}:{candle.timestamp.isoformat()}:{purpose}",
                )
                broker_res = execute_through_broker(
                    store,
                    portfolio_id=LIVE_SIM_VIRTUAL_PORTFOLIO_ID,
                    instrument=instrument,
                    direction=close_dir,
                    quantity=pos.quantity,
                    fill=fill,
                    execution_at=candle.timestamp,
                    timeframe=source,
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
                    fallback_cursors.pop(pos_id, None)
                    closed += 1
                    logger.info(
                        "Live-sim %s closed %s via %s",
                        source,
                        instrument.symbol,
                        purpose,
                    )
                else:
                    active_cursors[pos_id] = candle.timestamp.isoformat()
                break
            nested.commit()
        except Exception as exc:
            nested.rollback()
            logger.exception("Live-sim crypto protection failed for %s", pos_id)
            errors.append({"position_id": pos_id, "error": str(exc)})

    return {"checked": len(rows), "closed": closed, "errors": errors}


def run_crypto_fast_protection(store: TradingStore, now: datetime) -> dict[str, Any]:
    """Fetch 1m BTC/ETH candles and run SL/TP when open positions exist."""
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

    research_work = _collect_research_crypto_work(store)
    live_sim_rows = _collect_live_sim_crypto_rows(store)
    open_symbols = _symbols_with_open_crypto(research_work, live_sim_rows)
    mark_symbols = set(CONTINUOUS_MARK_CRYPTO_SYMBOLS) | open_symbols

    cursors = _get_fast_cursors(store)
    fallback_cursors = _get_5m_fallback_cursors(store)
    position_ids_by_instrument: dict[str, list[str]] = {}
    for position, _, instrument in research_work:
        position_ids_by_instrument.setdefault(instrument.id, []).append(position.id)
    for row in live_sim_rows:
        position_ids_by_instrument.setdefault(row["instrument_id"], []).append(row["id"])

    fetches = 0
    candles_1m_by_instrument: dict[str, list] = {}
    for db_sym in sorted(mark_symbols):
        instrument = store.get_instrument_by_symbol(db_sym)
        if not instrument:
            continue
        since = _fetch_since_for_instrument(
            store,
            instrument.id,
            position_ids_by_instrument.get(instrument.id, []),
            cursors,
            now,
        )
        fetched = _fetch_and_store_1m(store, instrument, since=since)
        fetches += 1
        stored = store.list_candles(
            instrument.id,
            FAST_PROTECTION_TIMEFRAME,
            since=since,
            limit=MAX_CANDLES_PER_POSITION_PER_RUN,
        )
        candles_1m_by_instrument[instrument.id] = stored or fetched

    # Durable market data BEFORE any position-close work can fail the transaction.
    _commit_market_data(store)

    stale_by_instrument: dict[str, bool] = {}
    freshness: dict[str, Any] = {}
    candles_5m_by_instrument: dict[str, list] = {}
    any_stale = False
    for db_sym in sorted(open_symbols):
        instrument = store.get_instrument_by_symbol(db_sym)
        if not instrument:
            continue
        candles_1m = candles_1m_by_instrument.get(instrument.id) or []
        latest_1m = latest_completed_candle_ts(candles_1m, FAST_PROTECTION_TIMEFRAME, now)
        if latest_1m is None:
            latest_1m = store.latest_candle_timestamp(
                instrument.id, FAST_PROTECTION_TIMEFRAME
            )
        age = crypto_1m_age_minutes(latest_1m, now)
        stale = is_crypto_1m_stale(latest_1m, now)
        stale_by_instrument[instrument.id] = stale
        source = "5m_fallback" if stale else "1m"
        freshness[db_sym] = {
            "latest_completed_1m": latest_1m.isoformat() if latest_1m else None,
            "age_minutes": age,
            "protection_source": source,
            "stale": stale,
        }
        if stale:
            any_stale = True
            # Load completed 5m bars from open/cursor for fail-safe exits.
            floors: list[datetime] = []
            for pid in position_ids_by_instrument.get(instrument.id, []):
                raw = fallback_cursors.get(pid) or cursors.get(pid)
                if raw:
                    floors.append(datetime.fromisoformat(raw.replace("Z", "+00:00")))
            for position, _, inst in research_work:
                if inst.id == instrument.id and position.opened_at:
                    floors.append(_as_utc(position.opened_at))
            for row in live_sim_rows:
                if row["instrument_id"] == instrument.id and row.get("opened_at"):
                    floors.append(_as_utc(row["opened_at"]))
            since_5m = min(floors) if floors else now - timedelta(hours=6)
            candles_5m_by_instrument[instrument.id] = store.list_candles(
                instrument.id,
                FALLBACK_TIMEFRAME,
                since=since_5m,
                limit=MAX_CANDLES_PER_POSITION_PER_RUN,
            )

    research_result = (
        _process_research_crypto(
            store,
            research_work,
            now=now,
            cursors=cursors,
            fallback_cursors=fallback_cursors,
            candles_1m_by_instrument=candles_1m_by_instrument,
            candles_5m_by_instrument=candles_5m_by_instrument,
            stale_by_instrument=stale_by_instrument,
        )
        if research_work
        else {"checked": 0, "closed": 0, "errors": [], "sources": {"1m": 0, "5m_fallback": 0}}
    )
    live_sim_result = (
        _process_live_sim_crypto(
            store,
            live_sim_rows,
            now=now,
            cursors=cursors,
            fallback_cursors=fallback_cursors,
            candles_1m_by_instrument=candles_1m_by_instrument,
            candles_5m_by_instrument=candles_5m_by_instrument,
            stale_by_instrument=stale_by_instrument,
        )
        if live_sim_rows
        else {"checked": 0, "closed": 0, "errors": []}
    )
    _save_fast_cursors(store, cursors)
    _save_5m_fallback_cursors(store, fallback_cursors)

    from quantara_engine.execution.crypto_mark_valuation import (
        apply_crypto_1m_marks,
        latest_completed_1m_close,
        prune_crypto_canonical_marks,
    )

    prune_crypto_canonical_marks(store, mark_symbols)
    marks_to_apply: dict[str, tuple[Decimal, datetime]] = {}
    for db_sym in mark_symbols:
        instrument = store.get_instrument_by_symbol(db_sym)
        if not instrument:
            continue
        candles = candles_1m_by_instrument.get(instrument.id) or []
        latest = latest_completed_1m_close(candles, now)
        if latest:
            marks_to_apply[db_sym] = latest

    mark_report = (
        apply_crypto_1m_marks(store, marks_to_apply, flush=False)
        if marks_to_apply
        else {"applied_symbols": []}
    )

    status = "success"
    if research_result.get("errors") or live_sim_result.get("errors"):
        status = "degraded"
    health_flag = "CRYPTO_1M_STALE" if any_stale and open_symbols else None

    return {
        "status": status,
        "fetches": fetches,
        "symbols": sorted(mark_symbols),
        "open_symbols": sorted(open_symbols),
        "research": research_result,
        "live_sim": live_sim_result,
        "marks_applied": mark_report.get("applied_symbols", []),
        "freshness": freshness,
        "health_flag": health_flag,
        "1m_stale_threshold_minutes": CRYPTO_1M_STALE_MINUTES,
    }
