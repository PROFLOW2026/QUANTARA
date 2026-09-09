"""Independent open-position SL/TP management and mark updates."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING

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

if TYPE_CHECKING:
    from quantara_engine.domain.types import Instrument, Position, StrategyInstance
    from quantara_engine.persistence.store import TradingStore

logger = logging.getLogger(__name__)

POSITION_MANAGEMENT_CURSORS_KEY = "position_management_cursors"
MAX_CANDLES_PER_POSITION_PER_RUN = 200


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
) -> None:
    cursors = _get_cursors(store)
    cursors[position_id] = timestamp.isoformat()
    store.update_settings(POSITION_MANAGEMENT_CURSORS_KEY, cursors)


def clear_position_management_cursor(store: TradingStore, position_id: str) -> None:
    cursors = _get_cursors(store)
    if position_id in cursors:
        del cursors[position_id]
        store.update_settings(POSITION_MANAGEMENT_CURSORS_KEY, cursors)


def _management_candles(
    store: TradingStore,
    position: Position,
    instrument: Instrument,
    timeframe: str,
    *,
    last_managed: datetime | None,
    now: datetime,
) -> list:
    floor = last_managed or position.opened_at
    if floor is None:
        return []
    candles = store.list_candles(
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


def _apply_mark(
    store: TradingStore,
    state,
    position: Position,
    candle,
) -> None:
    state.recalculate_equity({position.instrument_id: candle.close})
    position.current_price = candle.close
    store.update_open_position_mark(position.id, position.current_price, position.unrealized_pnl)
    store.update_portfolio(state.portfolio)


def process_position_management(
    store: TradingStore,
    *,
    position: Position,
    instance: StrategyInstance,
    instrument: Instrument,
    now: datetime,
    broker: PaperBrokerAdapter | None = None,
) -> dict:
    """Process completed candles since last management for one open position."""
    if position.status.value != "open":
        return {"position_id": position.id, "status": "skipped_not_open"}

    broker = broker or PaperBrokerAdapter(instrument.id, ExecutionAssumptions())
    last_managed = get_last_managed_timestamp(store, position.id)
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

    state = store.load_portfolio_state(position.portfolio_id)
    position = next(p for p in state.open_positions() if p.id == position.id)
    marks = 0

    for candle in candles:
        open_position = next(
            (p for p in state.open_positions() if p.id == position.id),
            None,
        )
        if open_position is None:
            set_last_managed_timestamp(store, position.id, candle.timestamp)
            break

        trigger = detect_exit_trigger(open_position, candle)
        if trigger:
            reason, trigger_price = trigger
            order, fill = broker.execute_exit_at_trigger(
                open_position.direction,
                open_position.quantity,
                candle,
                trigger_price,
                state.portfolio.id,
            )
            trade = state.close_position(open_position, fill, reason, candle.timestamp)
            store.persist_exit_execution(
                order=order,
                fill=fill,
                position=open_position,
                trade=trade,
                portfolio_state=state,
                strategy_instance_id=instance.id,
                filled_at=candle.timestamp,
            )
            decision_type = (
                DecisionType.SL_TRIGGERED if reason == ExitReason.SL else DecisionType.TP_TRIGGERED
            )
            store.save_decision(
                DecisionLogEntry(
                    id=new_id(),
                    strategy_instance_id=instance.id,
                    instrument_id=instrument.id,
                    candle_timestamp=candle.timestamp,
                    decision_type=decision_type,
                    message=f"{reason.value.upper()} hit at {trigger_price}",
                    signal_id=None,
                    metadata={"position_management": True},
                )
            )
            snap = state.create_snapshot(candle.timestamp)
            store.save_snapshot(snap)
            store.update_portfolio(state.portfolio)
            clear_position_management_cursor(store, position.id)
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

        _apply_mark(store, state, open_position, candle)
        set_last_managed_timestamp(store, position.id, candle.timestamp)
        marks += 1

    return {
        "position_id": position.id,
        "status": "managed",
        "marks_updated": marks,
        "candles_processed": marks,
    }


def manage_all_open_positions(
    store: TradingStore,
    now: datetime,
) -> dict:
    """Run SL/TP + mark management for every open paper position."""
    results: list[dict] = []
    errors: list[dict] = []
    positions_checked = 0
    exits = 0
    instrument_cache: dict[str, Instrument | None] = {}

    for entry in store.list_competition_entries():
        portfolio = entry["portfolio"]
        instance = entry["instance"]
        state = store.load_portfolio_state(portfolio.id)

        for position in state.open_positions():
            if position.strategy_instance_id != instance.id:
                continue
            positions_checked += 1
            if position.instrument_id not in instrument_cache:
                instrument_cache[position.instrument_id] = store.get_instrument_by_id(
                    position.instrument_id
                )
            instrument = instrument_cache[position.instrument_id]
            if not instrument:
                errors.append({"position_id": position.id, "error": "instrument_not_found"})
                continue
            try:
                result = process_position_management(
                    store,
                    position=position,
                    instance=instance,
                    instrument=instrument,
                    now=now,
                )
                results.append(result)
                if result.get("status") == "closed":
                    exits += 1
            except Exception as exc:
                logger.exception("Position management failed for %s", position.id)
                errors.append({"position_id": position.id, "error": str(exc)})

    return {
        "positions_checked": positions_checked,
        "exits": exits,
        "results": results,
        "errors": errors,
    }
