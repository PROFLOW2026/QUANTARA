"""Owner manual position close — real broker execution path, no DB-only closure."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from quantara_engine.broker.accounts import LIVE_SIM_10K_ACCOUNT_SLUG, LIVE_SIM_VIRTUAL_PORTFOLIO_ID
from quantara_engine.domain.types import (
    DecisionLogEntry,
    DecisionType,
    Direction,
    ExitReason,
    new_id,
)
from quantara_engine.execution.paper_broker import PaperBrokerAdapter
from quantara_engine.execution.position_management import clear_position_management_cursor
from quantara_engine.market_data.polling import is_bar_complete
from quantara_engine.market_data.registry import get_asset
from quantara_engine.market_data.sessions import session_allows_entries
from quantara_engine.persistence.store import TradingStore
from quantara_engine.portfolio.currency import build_currency_context

logger = logging.getLogger(__name__)

MANUAL_CLOSE_IDEMPOTENCY_PREFIX = "manual:close:"


@dataclass
class ManualCloseResult:
    position_id: str
    status: str
    detail: str | None = None
    fill_price: float | None = None
    realized_pnl: float | None = None
    broker_order_id: str | None = None
    symbol: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "position_id": self.position_id,
            "status": self.status,
            "detail": self.detail,
            "fill_price": self.fill_price,
            "realized_pnl": self.realized_pnl,
            "broker_order_id": self.broker_order_id,
            "symbol": self.symbol,
        }


def _latest_tradable_candle(
    store: TradingStore,
    instrument_id: str,
    timeframe: str,
    now: datetime,
):
    candles = store.list_recent_candles(instrument_id, timeframe, limit=5)
    for candle in reversed(candles):
        if is_bar_complete(candle.timestamp, timeframe, now):
            return candle
    return None


def _asset_tradable(symbol: str, ts: datetime) -> bool:
    asset = get_asset(symbol)
    if not asset:
        return True
    return session_allows_entries(asset.trading_sessions, ts)


def _manual_close_idempotency_key(position_id: str) -> str:
    return f"{MANUAL_CLOSE_IDEMPOTENCY_PREFIX}{position_id}"


def close_research_position(
    store: TradingStore,
    position_id: str,
    *,
    now: datetime | None = None,
) -> ManualCloseResult:
    """Close one competition strategy leg via broker reduce/close."""
    now = now or datetime.now(timezone.utc)
    position = store.get_open_position_by_id(position_id)
    if position is None:
        if store.trade_exists_for_position(position_id):
            return ManualCloseResult(position_id, "already_closed")
        return ManualCloseResult(position_id, "not_found")

    instrument = store.get_instrument_by_id(position.instrument_id)
    if not instrument:
        return ManualCloseResult(position_id, "not_found", detail="instrument_missing")

    instance = store.get_strategy_instance_by_id(position.strategy_instance_id)
    if not instance:
        return ManualCloseResult(position_id, "not_found", detail="strategy_instance_missing")

    if not _asset_tradable(instrument.symbol, now):
        return ManualCloseResult(
            position_id,
            "pending_market",
            detail="market_closed",
            symbol=instrument.symbol,
        )

    candle = _latest_tradable_candle(store, instrument.id, instance.timeframe, now)
    if candle is None:
        return ManualCloseResult(
            position_id,
            "pending_market",
            detail="no_tradable_candle",
            symbol=instrument.symbol,
        )

    state = store.load_portfolio_state(position.portfolio_id)
    open_pos = next((p for p in state.open_positions() if p.id == position.id), None)
    if open_pos is None:
        return ManualCloseResult(position_id, "already_closed", symbol=instrument.symbol)

    from quantara_engine.execution.cost_profile import execution_assumptions_for

    broker = PaperBrokerAdapter(instrument.id, execution_assumptions_for(instrument))
    order, fill = broker.execute_exit_at_trigger(
        open_pos.direction,
        open_pos.quantity,
        candle,
        candle.open,
        state.portfolio.id,
        gap_exit=True,
    )

    from quantara_engine.broker.execution_bridge import execute_through_broker

    close_dir = Direction.SHORT if open_pos.direction.value == "long" else Direction.LONG
    broker_res = execute_through_broker(
        store,
        portfolio_id=state.portfolio.id,
        instrument=instrument,
        direction=close_dir,
        quantity=open_pos.quantity,
        fill=fill,
        execution_at=candle.timestamp,
        timeframe=instance.timeframe,
        idempotency_key=_manual_close_idempotency_key(open_pos.id),
        is_close=True,
        strategy_position_id=open_pos.id,
        order_purpose="close",
    )

    if broker_res is not None and not broker_res.accepted:
        reason = (
            broker_res.decision.rejection_reason.value
            if broker_res.decision and broker_res.decision.rejection_reason
            else "broker_rejected"
        )
        return ManualCloseResult(
            position_id,
            "rejected",
            detail=reason,
            symbol=instrument.symbol,
            broker_order_id=broker_res.broker_order_id,
        )

    ctx = build_currency_context(store, [instrument])
    if broker_res is not None and broker_res.shadow_only:
        from quantara_engine.broker.lifecycle import close_shadow_position

        trade = close_shadow_position(
            state,
            open_pos,
            exit_reason=ExitReason.MANUAL,
            closed_at=candle.timestamp,
            currency=ctx,
            exit_price=candle.open,
        )
    else:
        trade = state.close_position(
            open_pos, fill, ExitReason.MANUAL, candle.timestamp, ctx
        )

    decision = DecisionLogEntry(
        id=new_id(),
        strategy_instance_id=instance.id,
        instrument_id=instrument.id,
        candle_timestamp=candle.timestamp,
        decision_type=DecisionType.TRADING_HALTED,
        message="manual_close",
        signal_id=None,
        metadata={
            "close_source": "manual",
            "requested_by": "owner",
            "exit_reason": ExitReason.MANUAL.value,
            "order_purpose": "close",
            "fill_price": float(fill.fill_price),
            "position_id": open_pos.id,
        },
    )

    store.persist_exit_execution(
        order=order,
        fill=fill,
        position=open_pos,
        trade=trade,
        portfolio_state=state,
        strategy_instance_id=instance.id,
        filled_at=candle.timestamp,
    )
    store.save_decision(decision)
    clear_position_management_cursor(store, open_pos.id)

    logger.info(
        "Manual close research %s %s at %s pnl=%s",
        instrument.symbol,
        open_pos.id,
        fill.fill_price,
        trade.realized_pnl,
    )

    return ManualCloseResult(
        position_id,
        "filled",
        fill_price=float(fill.fill_price),
        realized_pnl=float(trade.realized_pnl),
        broker_order_id=broker_res.broker_order_id if broker_res else None,
        symbol=instrument.symbol,
    )


def close_all_research_symbol_positions(
    store: TradingStore,
    db_symbol: str,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Flatten all open competition legs for one symbol."""
    now = now or datetime.now(timezone.utc)
    positions = store.list_open_competition_positions_for_symbol(db_symbol)
    results = [close_research_position(store, p.id, now=now) for p in positions]
    filled = sum(1 for r in results if r.status == "filled")
    pending = sum(1 for r in results if r.status == "pending_market")
    rejected = sum(1 for r in results if r.status == "rejected")
    remaining = len(store.list_open_competition_positions_for_symbol(db_symbol))
    return {
        "symbol": db_symbol,
        "attempted": len(positions),
        "filled": filled,
        "pending_market": pending,
        "rejected": rejected,
        "remaining_open": remaining,
        "results": [r.to_dict() for r in results],
    }


def close_live_sim_position(
    store: TradingStore,
    position_id: str,
    *,
    now: datetime | None = None,
) -> ManualCloseResult:
    """Close one live-sim position via broker."""
    from sqlalchemy import text

    now = now or datetime.now(timezone.utc)
    row = store.session.execute(
        text(
            """
            SELECT p.id::text, p.instrument_id::text, p.direction::text, p.quantity,
                   p.timeframe, i.symbol
            FROM live_sim_positions p
            JOIN instruments i ON i.id = p.instrument_id
            WHERE p.id = :pid AND p.status = 'open'
            """
        ),
        {"pid": position_id},
    ).mappings().first()
    if not row:
        return ManualCloseResult(position_id, "not_found")

    instrument = store.get_instrument_by_id(row["instrument_id"])
    if not instrument:
        return ManualCloseResult(position_id, "not_found", detail="instrument_missing")

    if not _asset_tradable(instrument.symbol, now):
        return ManualCloseResult(
            position_id,
            "pending_market",
            detail="market_closed",
            symbol=instrument.symbol,
        )

    candle = _latest_tradable_candle(store, instrument.id, row["timeframe"], now)
    if candle is None:
        return ManualCloseResult(
            position_id,
            "pending_market",
            detail="no_tradable_candle",
            symbol=instrument.symbol,
        )

    from decimal import Decimal as Dc

    direction = Direction.LONG if str(row["direction"]).lower() == "long" else Direction.SHORT
    qty = Dc(str(row["quantity"]))

    from quantara_engine.execution.cost_profile import execution_assumptions_for

    broker = PaperBrokerAdapter(instrument.id, execution_assumptions_for(instrument))
    close_dir = Direction.SHORT if direction == Direction.LONG else Direction.LONG
    order, fill = broker.execute_exit_at_trigger(
        direction,
        qty,
        candle,
        candle.open,
        LIVE_SIM_VIRTUAL_PORTFOLIO_ID,
        gap_exit=True,
    )

    from quantara_engine.broker.execution_bridge import execute_through_broker
    from quantara_engine.live_sim.opportunity import live_sim_execution_idempotency_key

    idem = live_sim_execution_idempotency_key(
        LIVE_SIM_10K_ACCOUNT_SLUG,
        f"manual:close:{position_id}",
    )
    broker_res = execute_through_broker(
        store,
        portfolio_id=LIVE_SIM_VIRTUAL_PORTFOLIO_ID,
        instrument=instrument,
        direction=close_dir,
        quantity=qty,
        fill=fill,
        execution_at=candle.timestamp,
        timeframe=row["timeframe"],
        idempotency_key=idem,
        is_close=True,
        strategy_position_id=position_id,
        order_purpose="close",
        skip_if_not_competition=False,
        account_slug=LIVE_SIM_10K_ACCOUNT_SLUG,
    )

    if broker_res is not None and not broker_res.accepted:
        reason = (
            broker_res.decision.rejection_reason.value
            if broker_res.decision and broker_res.decision.rejection_reason
            else "broker_rejected"
        )
        return ManualCloseResult(
            position_id,
            "rejected",
            detail=reason,
            symbol=instrument.symbol,
            broker_order_id=broker_res.broker_order_id,
        )

    store.session.execute(
        text(
            """
            UPDATE live_sim_positions
            SET status = 'closed', closed_at = :ts, updated_at = NOW()
            WHERE id = :id AND status = 'open'
            """
        ),
        {"id": position_id, "ts": candle.timestamp},
    )

    from quantara_engine.broker.execution_service import BrokerExecutionService

    svc = BrokerExecutionService(store, account_slug=LIVE_SIM_10K_ACCOUNT_SLUG)
    svc.mark_to_market({instrument.symbol.upper(): candle.close}, at=candle.timestamp)

    realized = float(broker_res.realized_pnl) if broker_res and broker_res.realized_pnl else None
    logger.info("Manual close live-sim %s %s at %s", instrument.symbol, position_id, fill.fill_price)

    return ManualCloseResult(
        position_id,
        "filled",
        fill_price=float(fill.fill_price),
        realized_pnl=realized,
        broker_order_id=broker_res.broker_order_id if broker_res else None,
        symbol=instrument.symbol,
    )


def close_all_live_sim_symbol_positions(
    store: TradingStore,
    db_symbol: str,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    from sqlalchemy import text

    now = now or datetime.now(timezone.utc)
    inst = store.get_instrument_by_symbol(db_symbol)
    if not inst:
        return {"symbol": db_symbol, "attempted": 0, "results": []}

    rows = store.session.execute(
        text(
            """
            SELECT p.id::text
            FROM live_sim_positions p
            WHERE p.instrument_id = :iid AND p.status = 'open'
            """
        ),
        {"iid": inst.id},
    ).mappings().all()
    results = [close_live_sim_position(store, r["id"], now=now) for r in rows]
    remaining = store.session.execute(
        text(
            """
            SELECT COUNT(*) FROM live_sim_positions
            WHERE instrument_id = :iid AND status = 'open'
            """
        ),
        {"iid": inst.id},
    ).scalar()
    return {
        "symbol": db_symbol,
        "attempted": len(rows),
        "filled": sum(1 for r in results if r.status == "filled"),
        "pending_market": sum(1 for r in results if r.status == "pending_market"),
        "rejected": sum(1 for r in results if r.status == "rejected"),
        "remaining_open": int(remaining or 0),
        "results": [r.to_dict() for r in results],
    }
