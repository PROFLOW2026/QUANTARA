"""1-minute SL/TP protection for open BTC/ETH positions (Research + Live Sim)."""

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
from quantara_engine.execution.exit_triggers import detect_exit_trigger
from quantara_engine.execution.paper_broker import PaperBrokerAdapter
from quantara_engine.execution.position_management import (
    MAX_CANDLES_PER_POSITION_PER_RUN,
    _management_candles,
    clear_position_management_cursor,
    process_position_management,
)
from quantara_engine.live_sim.opportunity import live_sim_execution_idempotency_key
from quantara_engine.market_data.adapters.coinbase import CoinbaseMarketDataProvider, CoinbaseError
from quantara_engine.market_data.polling import FAST_PROTECTION_TIMEFRAME
from quantara_engine.market_data.provider_budgets import FetchPriority
from quantara_engine.market_data.registry import get_asset
from quantara_engine.market_data.symbols import normalize_db_symbol
from quantara_engine.persistence.batch_summary import batch_open_positions_by_portfolio

if TYPE_CHECKING:
    from quantara_engine.domain.types import Instrument, Position, StrategyInstance
    from quantara_engine.persistence.store import TradingStore

logger = logging.getLogger(__name__)

CRYPTO_FAST_PROTECTION_CURSORS_KEY = "crypto_fast_protection_cursors"
FAST_CRYPTO_DB_SYMBOLS = frozenset({"BTCUSD", "ETHUSD"})
FAST_PROTECTION_IDEMPOTENCY_PREFIX = "pm1m"
FAST_FETCH_LOOKBACK_MINUTES = 10


def is_fast_protection_crypto(symbol: str) -> bool:
    return normalize_db_symbol(symbol) in FAST_CRYPTO_DB_SYMBOLS


def _get_fast_cursors(store: TradingStore) -> dict[str, str]:
    return dict(store.get_settings_dict().get(CRYPTO_FAST_PROTECTION_CURSORS_KEY) or {})


def _save_fast_cursors(store: TradingStore, cursors: dict[str, str]) -> None:
    store.update_settings(CRYPTO_FAST_PROTECTION_CURSORS_KEY, cursors, flush=False)


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
    return [dict(row) for row in rows if is_fast_protection_crypto(row["symbol"])]


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
    if floors:
        since = min(floors) - timedelta(minutes=1)
    else:
        since = now - timedelta(minutes=FAST_FETCH_LOOKBACK_MINUTES)
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


def _process_research_crypto(
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


def _process_live_sim_crypto(
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
    symbols_needed = _symbols_with_open_crypto(research_work, live_sim_rows)

    if not symbols_needed:
        return {
            "status": "skipped",
            "reason": "no_open_crypto_positions",
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

    fetches = 0
    candles_by_instrument: dict[str, list] = {}
    for db_sym in sorted(symbols_needed):
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
        candles_by_instrument[instrument.id] = stored or fetched

    research_result = _process_research_crypto(
        store,
        research_work,
        now=now,
        cursors=cursors,
        candles_by_instrument=candles_by_instrument,
    )
    live_sim_result = _process_live_sim_crypto(
        store,
        live_sim_rows,
        now=now,
        cursors=cursors,
        candles_by_instrument=candles_by_instrument,
    )
    _save_fast_cursors(store, cursors)

    broker_marks: dict[str, Decimal] = {}
    for _, _, instrument in research_work:
        candles = candles_by_instrument.get(instrument.id)
        if candles:
            broker_marks[instrument.symbol.upper()] = candles[-1].close
    if broker_marks:
        BrokerExecutionService(store).mark_to_market(broker_marks, at=now)

    return {
        "status": "success",
        "fetches": fetches,
        "symbols": sorted(symbols_needed),
        "research": research_result,
        "live_sim": live_sim_result,
    }
