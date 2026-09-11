"""Unified broker execution entry for all open/close paths."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from quantara_engine.broker.execution_service import BrokerExecutionResult, BrokerExecutionService
from quantara_engine.broker.integration import should_use_broker_realism
from quantara_engine.domain.types import Direction, Instrument, IntentStatus, OrderIntent
from quantara_engine.execution.fill_calculator import FillResult
from quantara_engine.persistence.store import TradingStore


def execute_through_broker(
    store: TradingStore,
    *,
    portfolio_id: str,
    instrument: Instrument,
    direction: Direction,
    quantity: Decimal,
    fill: FillResult,
    execution_at: datetime,
    timeframe: str,
    idempotency_key: str,
    is_close: bool = False,
    strategy_position_id: str | None = None,
    opportunity_key: str | None = None,
    order_purpose: str = "entry",
    is_liquidation: bool = False,
    skip_if_not_competition: bool = True,
) -> BrokerExecutionResult | None:
    """
    Canonical broker gate + fill persistence.

    Returns None when broker realism does not apply (non-competition portfolios).
    """
    if skip_if_not_competition and not should_use_broker_realism(portfolio_id):
        return None

    intent = OrderIntent(
        id=idempotency_key,
        signal_id="",
        strategy_instance_id="",
        portfolio_id=portfolio_id,
        direction=direction,
        quantity=quantity,
        stop_loss=Decimal("0"),
        take_profit=None,
        target_risk_amount=Decimal("0"),
        actual_risk_amount=Decimal("0"),
        signal_candle_timestamp=execution_at,
        risk_profile_id="",
        status=IntentStatus.PENDING_EXECUTION,
        is_close=is_close,
        position_id=strategy_position_id,
    )
    svc = BrokerExecutionService(store)
    return svc.execute_order(
        intent=intent,
        instrument=instrument,
        fill=fill,
        execution_at=execution_at,
        timeframe=timeframe,
        idempotency_key=idempotency_key,
        opportunity_key=opportunity_key,
        strategy_position_id=strategy_position_id,
        order_purpose=order_purpose,
        is_liquidation=is_liquidation,
    )


def broker_required(portfolio_id: str) -> bool:
    return should_use_broker_realism(portfolio_id)
