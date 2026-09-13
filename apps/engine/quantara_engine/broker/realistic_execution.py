"""Realistic broker order lifecycle orchestration for BrokerExecutionService."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import text

from quantara_engine.broker.broker_authority import persist_authority_order
from quantara_engine.broker.broker_adapter_contract import BrokerSubmitRequest
from quantara_engine.broker.execution_model import ExecutionModelVersion
from quantara_engine.broker.execution_product import ExecutionProductRoute
from quantara_engine.broker.execution_timing import lifecycle_timestamps
from quantara_engine.broker.simulated_broker_adapter import SimulatedBrokerAdapter, adapter_for
from quantara_engine.execution.cost_profile import execution_assumptions_for
from quantara_engine.execution.fill_calculator import FillResult
from quantara_engine.persistence.store import TradingStore

_ADAPTERS: dict[str, SimulatedBrokerAdapter] = {}


def broker_adapter_for(
    store: TradingStore,
    account_slug: str,
    account_id: str,
    execution_model: ExecutionModelVersion,
) -> SimulatedBrokerAdapter:
    key = f"{account_slug}:{account_id}"
    if key not in _ADAPTERS:
        _ADAPTERS[key] = adapter_for(store, account_slug, account_id, execution_model)
    return _ADAPTERS[key]


def insert_broker_order(
    session,
    *,
    order_id: str,
    account_id: str,
    instrument_id: str,
    direction: str,
    quantity: Decimal,
    idempotency_key: str,
    client_order_id: str,
    execution_product: str,
    execution_model: str,
    order_purpose: str,
    execution_at: datetime,
    db_strategy_intent_id: str | None,
    db_strategy_portfolio_id: str | None,
) -> None:
    session.execute(
        text(
            """
            INSERT INTO broker_orders (
              id, broker_account_id, strategy_intent_id, strategy_portfolio_id,
              instrument_id, direction, requested_quantity, status,
              idempotency_key, client_order_id, execution_product, execution_model,
              order_type, time_in_force, reduce_only, remaining_quantity,
              order_purpose, submitted_at
            ) VALUES (
              :id, :aid, :iid, :pid, :inst, :dir, :qty, 'validating',
              :key, :cid, CAST(:product AS execution_product),
              CAST(:model AS execution_model_version),
              'market', 'day', FALSE, :qty,
              :purpose, :sub
            )
            """
        ),
        {
            "id": order_id,
            "aid": account_id,
            "iid": db_strategy_intent_id,
            "pid": db_strategy_portfolio_id,
            "inst": instrument_id,
            "dir": direction,
            "qty": quantity,
            "key": idempotency_key,
            "cid": client_order_id,
            "product": execution_product,
            "model": execution_model,
            "purpose": order_purpose,
            "sub": execution_at,
        },
    )


def submit_to_simulated_broker(
    store: TradingStore,
    *,
    account_slug: str,
    account_id: str,
    execution_model: ExecutionModelVersion,
    broker_order_id: str,
    client_order_id: str,
    symbol: str,
    direction: str,
    quantity: Decimal,
    mark_price: Decimal,
    route: ExecutionProductRoute,
    pre_accepted: bool,
    execution_at: datetime,
    instrument,
    is_close: bool,
    simulate_lost_response: bool = False,
) -> tuple[SimulatedBrokerAdapter, object]:
    adapter = broker_adapter_for(store, account_slug, account_id, execution_model)
    req = BrokerSubmitRequest(
        client_order_id=client_order_id,
        account_slug=account_slug,
        symbol=symbol,
        direction=direction,
        quantity=quantity,
        execution_product=route.product,
        mark_price=mark_price,
        reduce_only=is_close,
    )
    accepted_at, _ = lifecycle_timestamps(execution_at)
    result = adapter.submit_order(
        req,
        pre_accepted=pre_accepted,
        broker_order_id=broker_order_id,
        execution_at=execution_at,
        simulate_lost_response=simulate_lost_response,
    )
    if not result.submission_unknown:
        persist_authority_order(
            store.session,
            broker_account_id=account_id,
            broker_order_id=broker_order_id,
            client_order_id=client_order_id,
            status=result.status,
            submission_unknown=result.submission_unknown,
            accepted_at=accepted_at if pre_accepted else None,
        )
    return adapter, result


def update_order_status(session, order_id: str, status: str, **extra) -> None:
    sets = ["status = :st", "updated_at = NOW()"]
    params: dict = {"id": order_id, "st": status}
    if "accepted_at" in extra:
        sets.append("accepted_at = :accepted_at")
        params["accepted_at"] = extra["accepted_at"]
    if "filled_quantity" in extra:
        sets.append("filled_quantity = :fq")
        params["fq"] = extra["filled_quantity"]
    if "remaining_quantity" in extra:
        sets.append("remaining_quantity = :rq")
        params["rq"] = extra["remaining_quantity"]
    if "filled_at" in extra:
        sets.append("filled_at = :filled_at")
        params["filled_at"] = extra["filled_at"]
    if "submission_unknown" in extra:
        sets.append("submission_unknown = :su")
        params["su"] = extra["submission_unknown"]
    session.execute(
        text(f"UPDATE broker_orders SET {', '.join(sets)} WHERE id = :id"),
        params,
    )


def aggregate_fill_result(slices) -> FillResult:
    if not slices:
        raise ValueError("no fill slices")
    total_qty = sum(s.quantity for s in slices)
    if total_qty <= 0:
        raise ValueError("zero quantity")
    weighted = sum(s.quantity * s.fill.fill_price for s in slices) / total_qty
    return FillResult(
        fill_price=weighted.quantize(Decimal("0.00000001")),
        base_price=slices[0].fill.base_price,
        spread_cost=sum(s.fill.spread_cost for s in slices),
        slippage=sum(s.fill.slippage for s in slices),
        fees=sum(s.fill.fees for s in slices),
    )
