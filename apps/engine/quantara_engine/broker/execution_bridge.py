"""Unified broker execution entry for all open/close paths."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from quantara_engine.broker.attribution import resolve_physical_exit_quantity
from quantara_engine.broker.provenance import coerce_uuid
from quantara_engine.broker.execution_service import BrokerExecutionResult, BrokerExecutionService
from quantara_engine.broker.integration import should_use_broker_realism
from quantara_engine.domain.types import Direction, Instrument, IntentStatus, OrderIntent
from quantara_engine.execution.fill_calculator import FillResult
from quantara_engine.persistence.store import TradingStore


def canonical_fill_from_result(local_fill: FillResult, result: BrokerExecutionResult) -> FillResult:
    """Prefer persisted broker fill values (especially on idempotent retry)."""
    if not result.accepted or result.fill_price is None or result.fill_quantity is None:
        return local_fill
    if result.from_existing_fill or result.broker_fill_id:
        return FillResult(
            fill_price=result.fill_price,
            base_price=result.fill_price,
            spread_cost=result.spread_cost,
            slippage=result.slippage,
            fees=result.fees,
        )
    return local_fill


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
    strategy_intent_id: str | None = None,
    is_close: bool = False,
    strategy_position_id: str | None = None,
    opportunity_key: str | None = None,
    order_purpose: str = "entry",
    is_liquidation: bool = False,
    skip_if_not_competition: bool = True,
    account_slug: str | None = None,
) -> BrokerExecutionResult | None:
    """
    Canonical broker gate + fill persistence.

    Returns None when broker realism does not apply (non-competition portfolios).
    """
    if skip_if_not_competition and not should_use_broker_realism(portfolio_id):
        return None

    svc = BrokerExecutionService(store, account_slug=account_slug)
    account_id = svc.get_account_id()
    if is_close and account_id:
        physical_qty = resolve_physical_exit_quantity(
            store,
            broker_account_id=account_id,
            symbol=instrument.symbol,
            requested_quantity=quantity,
            strategy_position_id=strategy_position_id,
            portfolio_id=portfolio_id,
            opportunity_key=opportunity_key,
        )
        if physical_qty <= 0:
            return BrokerExecutionResult(
                accepted=True,
                shadow_only=True,
                fill_quantity=Decimal("0"),
                physical_opened_qty=Decimal("0"),
            )
        quantity = physical_qty

    domain_intent_id = coerce_uuid(strategy_intent_id) or str(uuid.uuid4())
    intent = OrderIntent(
        id=domain_intent_id,
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
    return svc.execute_order(
        intent=intent,
        instrument=instrument,
        fill=fill,
        execution_at=execution_at,
        timeframe=timeframe,
        idempotency_key=idempotency_key,
        strategy_intent_id=strategy_intent_id,
        opportunity_key=opportunity_key,
        strategy_position_id=strategy_position_id,
        order_purpose=order_purpose,
        is_liquidation=is_liquidation,
    )


def broker_required(portfolio_id: str) -> bool:
    return should_use_broker_realism(portfolio_id)
