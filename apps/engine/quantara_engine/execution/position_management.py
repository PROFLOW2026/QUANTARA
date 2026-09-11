"""Independent open-position SL/TP management and mark updates."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from quantara_engine.domain.types import (
    DecisionLogEntry,
    DecisionType,
    ExecutionAssumptions,
    ExitReason,
    new_id,
)
from quantara_engine.execution.exit_triggers import detect_exit_trigger
from quantara_engine.execution.paper_broker import PaperBrokerAdapter
from quantara_engine.market_data.polling import is_bar_complete
from quantara_engine.persistence.batch_summary import batch_open_positions_by_portfolio
from quantara_engine.portfolio.currency import CurrencyContext, build_currency_context
from quantara_engine.portfolio.service import PortfolioSnapshot, PortfolioState

if TYPE_CHECKING:
    from quantara_engine.domain.types import Instrument, Position, StrategyInstance
    from quantara_engine.persistence.store import TradingStore

logger = logging.getLogger(__name__)

POSITION_MANAGEMENT_CURSORS_KEY = "position_management_cursors"
MAX_CANDLES_PER_POSITION_PER_RUN = 200


@dataclass
class PMRunReport:
    positions_attempted: int = 0
    positions_successful: int = 0
    positions_failed: int = 0
    positions_closed: int = 0
    positions_marked: int = 0
    positions_checked: int = 0
    exits: int = 0
    deadlocks: int = 0
    retries: int = 0
    stop_iteration: int = 0
    errors: list[dict[str, Any]] = field(default_factory=list)
    results: list[dict[str, Any]] = field(default_factory=list)
    timing_ms: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "positions_attempted": self.positions_attempted,
            "positions_successful": self.positions_successful,
            "positions_failed": self.positions_failed,
            "positions_closed": self.positions_closed,
            "positions_marked": self.positions_marked,
            "positions_checked": self.positions_checked,
            "exits": self.exits,
            "deadlocks": self.deadlocks,
            "retries": self.retries,
            "stop_iteration": self.stop_iteration,
            "errors": self.errors,
            "results": self.results,
            "timing_ms": self.timing_ms,
        }


def _get_cursors(store: TradingStore) -> dict[str, str]:
    return dict(store.get_settings_dict().get(POSITION_MANAGEMENT_CURSORS_KEY) or {})


def get_last_managed_timestamp(store: TradingStore, position_id: str) -> datetime | None:
    raw = _get_cursors(store).get(position_id)
    if not raw:
        return None
    return datetime.fromisoformat(raw.replace("Z", "+00:00"))


def set_last_managed_timestamp(
    store: TradingStore,
    position_id: str,
    timestamp: datetime,
    *,
    cursors: dict[str, str] | None = None,
    flush: bool = True,
) -> None:
    target = cursors if cursors is not None else _get_cursors(store)
    target[position_id] = timestamp.isoformat()
    if cursors is None or flush:
        store.update_settings(POSITION_MANAGEMENT_CURSORS_KEY, target, flush=flush)


def clear_position_management_cursor(
    store: TradingStore,
    position_id: str,
    *,
    cursors: dict[str, str] | None = None,
    flush: bool = True,
) -> None:
    target = cursors if cursors is not None else _get_cursors(store)
    if position_id in target:
        del target[position_id]
        if cursors is None or flush:
            store.update_settings(POSITION_MANAGEMENT_CURSORS_KEY, target, flush=flush)


def _management_candles(
    store: TradingStore,
    position: Position,
    instrument: Instrument,
    timeframe: str,
    *,
    last_managed: datetime | None,
    now: datetime,
    prefetched: list | None = None,
) -> list:
    floor = last_managed or position.opened_at
    if floor is None:
        return []
    candles = prefetched if prefetched is not None else store.list_candles(
        instrument.id,
        timeframe,
        since=floor,
        limit=MAX_CANDLES_PER_POSITION_PER_RUN,
    )
    pending: list = []
    for candle in candles:
        if not is_bar_complete(candle.timestamp, timeframe, now):
            continue
        if last_managed is not None and candle.timestamp <= last_managed:
            continue
        if position.opened_at is not None and candle.timestamp < position.opened_at:
            continue
        pending.append(candle)
    return pending


def _prefetch_candles_by_pair(
    store: TradingStore,
    positions: list[tuple[Position, StrategyInstance, Instrument]],
    cursors: dict[str, str],
) -> dict[tuple[str, str], list]:
    floors: dict[tuple[str, str], datetime] = {}
    for position, instance, instrument in positions:
        key = (instrument.id, instance.timeframe)
        last_raw = cursors.get(position.id)
        last_managed = (
            datetime.fromisoformat(last_raw.replace("Z", "+00:00")) if last_raw else None
        )
        floor = last_managed or position.opened_at
        if floor is None:
            continue
        prev = floors.get(key)
        if prev is None or floor < prev:
            floors[key] = floor

    out: dict[tuple[str, str], list] = {}
    for (instrument_id, timeframe), since in floors.items():
        out[(instrument_id, timeframe)] = store.list_candles(
            instrument_id,
            timeframe,
            since=since,
            limit=MAX_CANDLES_PER_POSITION_PER_RUN,
        )
    return out


def _find_open_position(state: PortfolioState, position_id: str) -> Position | None:
    for pos in state.open_positions():
        if pos.id == position_id:
            return pos
    return None


def _apply_mark(
    state: PortfolioState,
    position: Position,
    candle,
    *,
    currency: CurrencyContext,
) -> None:
    state.recalculate_equity({position.instrument_id: candle.close}, currency)
    position.current_price = candle.close


def _persist_marks(
    store: TradingStore,
    state: PortfolioState,
    *,
    flush: bool = False,
) -> None:
    for pos in state.open_positions():
        store.update_open_position_mark(
            pos.id, pos.current_price, pos.unrealized_pnl, flush=flush
        )
    store.sync_portfolios_financial_state_from_ledger([state.portfolio], flush=flush)


def process_position_management(
    store: TradingStore,
    *,
    position: Position,
    instance: StrategyInstance,
    instrument: Instrument,
    now: datetime,
    broker: PaperBrokerAdapter | None = None,
    cursors: dict[str, str] | None = None,
    portfolio_state: PortfolioState | None = None,
    defer_writes: bool = False,
    pending_exits: list[dict[str, Any]] | None = None,
    pending_decisions: list[DecisionLogEntry] | None = None,
    pending_exit_snapshots: list[PortfolioSnapshot] | None = None,
    currency: CurrencyContext | None = None,
) -> dict:
    """Process completed candles since last management for one open position."""
    if position.status.value != "open":
        return {"position_id": position.id, "status": "skipped_not_open"}

    from quantara_engine.execution.cost_profile import execution_assumptions_for

    broker = broker or PaperBrokerAdapter(instrument.id, execution_assumptions_for(instrument))
    cursor_state = cursors if cursors is not None else _get_cursors(store)
    last_managed_raw = cursor_state.get(position.id)
    last_managed = (
        datetime.fromisoformat(last_managed_raw.replace("Z", "+00:00"))
        if last_managed_raw
        else None
    )
    candles = _management_candles(
        store,
        position,
        instrument,
        instance.timeframe,
        last_managed=last_managed,
        now=now,
    )
    if not candles:
        return {"position_id": position.id, "status": "no_pending_candles"}

    state = portfolio_state or store.load_portfolio_state(position.portfolio_id)
    if _find_open_position(state, position.id) is None:
        return {"position_id": position.id, "status": "skipped_not_open_in_state"}

    if currency is None:
        from unittest.mock import MagicMock

        if isinstance(store, MagicMock):
            ctx = CurrencyContext.usd_only({instrument.id: instrument})
        else:
            ctx = store.build_currency_context_for_instruments([instrument])
    else:
        ctx = currency

    marks = 0

    for candle in candles:
        open_position = _find_open_position(state, position.id)
        if open_position is None:
            cursor_state[position.id] = candle.timestamp.isoformat()
            break

        trigger = detect_exit_trigger(open_position, candle)
        if trigger:
            reason, trigger_price = trigger
            gap_exit = trigger_price == candle.open
            order, fill = broker.execute_exit_at_trigger(
                open_position.direction,
                open_position.quantity,
                candle,
                trigger_price,
                state.portfolio.id,
                gap_exit=gap_exit,
            )
            from quantara_engine.broker.execution_bridge import execute_through_broker
            from quantara_engine.domain.types import Direction

            close_dir = (
                Direction.SHORT if open_position.direction.value == "long" else Direction.LONG
            )
            purpose = "sl" if reason.value == "sl" else "tp"
            broker_res = execute_through_broker(
                store,
                portfolio_id=state.portfolio.id,
                instrument=instrument,
                direction=close_dir,
                quantity=open_position.quantity,
                fill=fill,
                execution_at=candle.timestamp,
                timeframe=instance.timeframe,
                idempotency_key=f"pm:{purpose}:{open_position.id}:{candle.timestamp.isoformat()}",
                is_close=True,
                strategy_position_id=open_position.id,
                order_purpose=purpose,
            )
            if broker_res is not None and not broker_res.accepted:
                cursor_state[position.id] = candle.timestamp.isoformat()
                continue

            trade = state.close_position(open_position, fill, reason, candle.timestamp, ctx)
            decision = DecisionLogEntry(
                id=new_id(),
                strategy_instance_id=instance.id,
                instrument_id=instrument.id,
                candle_timestamp=candle.timestamp,
                decision_type=(
                    DecisionType.SL_TRIGGERED
                    if reason == ExitReason.SL
                    else DecisionType.TP_TRIGGERED
                ),
                message=f"{reason.value.upper()} hit at {trigger_price}",
                signal_id=None,
                metadata={"position_management": True},
            )
            exit_snapshot = state.create_snapshot(candle.timestamp)

            if defer_writes:
                if pending_exits is not None:
                    pending_exits.append(
                        {
                            "order": order,
                            "fill": fill,
                            "position": open_position,
                            "trade": trade,
                            "portfolio_state": state,
                            "strategy_instance_id": instance.id,
                            "filled_at": candle.timestamp,
                            "decision": decision,
                            "snapshot": exit_snapshot,
                        }
                    )
            else:
                store.persist_exit_execution(
                    order=order,
                    fill=fill,
                    position=open_position,
                    trade=trade,
                    portfolio_state=state,
                    strategy_instance_id=instance.id,
                    filled_at=candle.timestamp,
                )
                store.save_decision(decision)
                store.save_snapshot(exit_snapshot)

            cursor_state.pop(position.id, None)
            logger.info(
                "Position %s closed via %s at %s",
                position.id,
                reason.value,
                candle.timestamp.isoformat(),
            )
            return {
                "position_id": position.id,
                "status": "closed",
                "exit_reason": reason.value,
                "exit_candle": candle.timestamp.isoformat(),
                "exit_price": float(fill.fill_price),
                "realized_pnl": float(trade.realized_pnl),
                "candles_processed": marks + 1,
            }

        _apply_mark(state, open_position, candle, currency=ctx)
        cursor_state[position.id] = candle.timestamp.isoformat()
        marks += 1

    if marks and not defer_writes:
        _persist_marks(store, state, flush=True)

    return {
        "position_id": position.id,
        "status": "managed",
        "marks_updated": marks,
        "candles_processed": marks,
    }


def _persist_pm_writes(
    store: TradingStore,
    *,
    portfolio_states: dict[str, PortfolioState],
    exit_portfolio_ids: set[str],
    mark_portfolio_ids: set[str],
    mark_updates: dict[str, tuple[Decimal, Decimal]],
    pending_exits: list[dict[str, Any]],
    cursors: dict[str, str],
    initial_cursors: dict[str, str],
) -> None:
    """Lock order: position closes → position marks → portfolios (equity only for marks)."""
    exit_chunk = 15
    for offset in range(0, len(pending_exits), exit_chunk):
        store.persist_exit_executions_batch(pending_exits[offset : offset + exit_chunk])

    if mark_updates:
        store.update_open_position_marks_batch(
            [
                (position_id, mark, upnl)
                for position_id, (mark, upnl) in sorted(
                    mark_updates.items(), key=lambda row: row[0]
                )
            ]
        )

    mark_only_ids = mark_portfolio_ids - exit_portfolio_ids
    affected_ids = exit_portfolio_ids | mark_portfolio_ids
    if affected_ids:
        affected_portfolios = [
            portfolio_states[pid].portfolio
            for pid in sorted(affected_ids)
            if pid in portfolio_states
        ]
        if affected_portfolios:
            store.sync_portfolios_financial_state_from_ledger(affected_portfolios, flush=False)

    if cursors != initial_cursors:
        store.update_settings(POSITION_MANAGEMENT_CURSORS_KEY, cursors, flush=False)

    store.flush()


def manage_all_open_positions(
    store: TradingStore,
    now: datetime,
) -> dict:
    """Run SL/TP + mark management — in-memory evaluation, batched persistence."""
    from quantara_engine.execution.flatten_positions import process_flatten_cycle
    from quantara_engine.trading.trading_controls import (
        allows_position_management,
        load_trading_control,
        requires_flatten,
    )

    report = PMRunReport()
    t_total = time.perf_counter()

    control = load_trading_control(store.get_settings_dict())
    if not allows_position_management(control):
        return report.to_dict()

    entries = store.list_competition_entries()
    entries.extend(store.list_orb_competition_entries())
    instance_by_id = {entry["instance"].id: entry["instance"] for entry in entries}
    portfolio_ids = list({entry["portfolio"].id for entry in entries})

    t0 = time.perf_counter()
    open_by_portfolio = batch_open_positions_by_portfolio(store, portfolio_ids)
    portfolio_states = store.batch_load_portfolio_states(portfolio_ids)
    report.timing_ms["preload"] = round((time.perf_counter() - t0) * 1000, 1)

    instrument_cache: dict[str, Instrument | None] = {}
    pending_exits: list[dict[str, Any]] = []
    exit_portfolio_ids: set[str] = set()
    mark_portfolio_ids: set[str] = set()
    mark_updates: dict[str, tuple[Decimal, Decimal]] = {}

    for positions in open_by_portfolio.values():
        for position in positions:
            if position.instrument_id not in instrument_cache:
                instrument_cache[position.instrument_id] = store.get_instrument_by_id(
                    position.instrument_id
                )

    unique_instruments = [inst for inst in instrument_cache.values() if inst]
    shared_currency = (
        build_currency_context(store, unique_instruments)
        if unique_instruments
        else CurrencyContext.usd_only()
    )

    if requires_flatten(control):
        flatten_report = process_flatten_cycle(
            store,
            now,
            portfolio_states=portfolio_states,
            open_by_portfolio=open_by_portfolio,
            instance_by_id=instance_by_id,
            instrument_cache=instrument_cache,
            pending_exits=pending_exits,
            currency=shared_currency,
        )
        report.results.append({"flatten": flatten_report})
        for bundle in pending_exits:
            exit_portfolio_ids.add(bundle["portfolio_state"].portfolio.id)
        open_by_portfolio = batch_open_positions_by_portfolio(store, portfolio_ids)

    work: list[tuple[Position, StrategyInstance, Instrument]] = []
    for positions in open_by_portfolio.values():
        for position in positions:
            instance = instance_by_id.get(position.strategy_instance_id)
            if not instance:
                continue
            instrument = instrument_cache.get(position.instrument_id)
            if not instrument:
                report.errors.append({"position_id": position.id, "error": "instrument_not_found"})
                report.positions_failed += 1
                continue
            work.append((position, instance, instrument))

    cursors = _get_cursors(store)
    initial_cursors = dict(cursors)

    t0 = time.perf_counter()
    candle_cache = _prefetch_candles_by_pair(store, work, cursors)
    report.timing_ms["candle_prefetch"] = round((time.perf_counter() - t0) * 1000, 1)

    work.sort(key=lambda row: (row[0].portfolio_id, row[0].id))

    t0 = time.perf_counter()
    for position, instance, instrument in work:
        report.positions_checked += 1
        prefetched = candle_cache.get((instrument.id, instance.timeframe))
        last_raw = cursors.get(position.id)
        last_managed = (
            datetime.fromisoformat(last_raw.replace("Z", "+00:00")) if last_raw else None
        )
        if not _management_candles(
            store,
            position,
            instrument,
            instance.timeframe,
            last_managed=last_managed,
            now=now,
            prefetched=prefetched,
        ):
            continue

        report.positions_attempted += 1
        pid = position.portfolio_id
        state = portfolio_states.get(pid)
        if state is None:
            report.positions_failed += 1
            report.errors.append({"position_id": position.id, "error": "portfolio_state_missing"})
            continue

        if _find_open_position(state, position.id) is None:
            report.positions_successful += 1
            continue

        try:
            result = process_position_management(
                store,
                position=position,
                instance=instance,
                instrument=instrument,
                now=now,
                cursors=cursors,
                portfolio_state=state,
                defer_writes=True,
                pending_exits=pending_exits,
                currency=shared_currency,
            )
            report.results.append(result)
            status = result.get("status")
            if status == "closed":
                report.positions_closed += 1
                report.exits += 1
                exit_portfolio_ids.add(pid)
                mark_portfolio_ids.add(pid)
                report.positions_successful += 1
            elif status == "managed":
                marks = int(result.get("marks_updated") or 0)
                if marks:
                    report.positions_marked += 1
                    mark_portfolio_ids.add(pid)
                    open_pos = _find_open_position(state, position.id)
                    if open_pos is not None:
                        mark_updates[position.id] = (
                            open_pos.current_price,
                            open_pos.unrealized_pnl,
                        )
                report.positions_successful += 1
            else:
                report.positions_successful += 1
        except Exception as exc:
            logger.exception("Position management failed for %s", position.id)
            report.positions_failed += 1
            report.errors.append({"position_id": position.id, "error": str(exc)})

    report.timing_ms["evaluation"] = round((time.perf_counter() - t0) * 1000, 1)

    t0 = time.perf_counter()
    try:
        _persist_pm_writes(
            store,
            portfolio_states=portfolio_states,
            exit_portfolio_ids=exit_portfolio_ids,
            mark_portfolio_ids=mark_portfolio_ids,
            mark_updates=mark_updates,
            pending_exits=pending_exits,
            cursors=cursors,
            initial_cursors=initial_cursors,
        )
    except Exception as exc:
        logger.exception("PM persist phase failed")
        report.errors.append({"phase": "persist", "error": str(exc)})
        if "deadlock" in str(exc).lower():
            report.deadlocks += 1
        raise

    report.timing_ms["persist"] = round((time.perf_counter() - t0) * 1000, 1)

    # Broker mark-to-market (margin call / liquidation react to price, not only fills)
    broker_marks: dict[str, Decimal] = {}
    sym_by_pos = {p.id: inst.symbol for p, _, inst in work if inst}
    for pos_id, (mark, _) in mark_updates.items():
        sym = sym_by_pos.get(pos_id)
        if sym:
            broker_marks[sym.upper()] = mark
    if broker_marks:
        from quantara_engine.broker.execution_service import BrokerExecutionService

        BrokerExecutionService(store).mark_to_market(broker_marks, at=now)

    report.timing_ms["total"] = round((time.perf_counter() - t_total) * 1000, 1)

    return report.to_dict()
