"""Authoritative simulated external broker — separate from QUANTARA strategy state."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from quantara_engine.broker.broker_adapter_contract import (
    BrokerSubmitRequest,
    BrokerSubmitResult,
    ReconciliationReport,
)
from quantara_engine.broker.client_order_id import derive_client_order_id
from quantara_engine.broker.deterministic_fill_engine import plan_deterministic_fills
from quantara_engine.broker.execution_model import ExecutionModelVersion, uses_realistic_broker
from quantara_engine.broker.execution_product import EXECUTION_SIM_DEFAULTS, ExecutionProduct
from quantara_engine.broker.live_execution_gate import assert_no_external_submission
from quantara_engine.broker.order_lifecycle import can_transition
from quantara_engine.broker.types import BrokerOrderStatus
from quantara_engine.domain.types import Direction, ExecutionAssumptions
from quantara_engine.execution.fill_calculator import FillResult


@dataclass
class SimulatedBrokerOrder:
    broker_order_id: str
    client_order_id: str
    account_slug: str
    symbol: str
    direction: str
    requested_quantity: Decimal
    filled_quantity: Decimal = Decimal("0")
    status: BrokerOrderStatus = BrokerOrderStatus.CREATED
    execution_product: ExecutionProduct | None = None
    submission_unknown: bool = False
    fills: list[tuple[int, Decimal, FillResult]] = field(default_factory=list)
    created_at: datetime | None = None
    accepted_at: datetime | None = None
    filled_at: datetime | None = None


class SimulatedBrokerEngine:
    """
    In-memory authoritative broker state.

    QUANTARA submits orders here; fills/positions are reconciled back via BrokerExecutionService.
    """

    def __init__(self, *, execution_model: ExecutionModelVersion) -> None:
        self.execution_model = execution_model
        self._orders_by_client: dict[str, SimulatedBrokerOrder] = {}
        self._orders_by_id: dict[str, SimulatedBrokerOrder] = {}
        self._halted = False

    def is_halted(self) -> bool:
        return self._halted

    def set_halted(self, halted: bool) -> None:
        self._halted = halted

    def get_order_by_client_id(self, client_order_id: str) -> SimulatedBrokerOrder | None:
        return self._orders_by_client.get(client_order_id)

    def get_order(self, broker_order_id: str) -> SimulatedBrokerOrder | None:
        return self._orders_by_id.get(broker_order_id)

    def submit_order(
        self,
        request: BrokerSubmitRequest,
        *,
        pre_accepted: bool,
        assumptions: ExecutionAssumptions,
        execution_at: datetime,
        simulate_lost_response: bool = False,
    ) -> BrokerSubmitResult:
        assert_no_external_submission()
        if self._halted and not request.reduce_only:
            return BrokerSubmitResult(accepted=False, status="rejected")

        existing = self._orders_by_client.get(request.client_order_id)
        if existing:
            return self._result_from_existing(existing)

        if not pre_accepted:
            return BrokerSubmitResult(accepted=False, status="rejected")

        broker_order_id = str(uuid.uuid4())
        order = SimulatedBrokerOrder(
            broker_order_id=broker_order_id,
            client_order_id=request.client_order_id,
            account_slug=request.account_slug,
            symbol=request.symbol,
            direction=request.direction,
            requested_quantity=request.quantity,
            execution_product=request.execution_product,
            created_at=execution_at,
        )
        self._register(order)

        self._transition(order, BrokerOrderStatus.VALIDATING)
        self._transition(order, BrokerOrderStatus.ACCEPTED)
        order.accepted_at = execution_at
        self._transition(order, BrokerOrderStatus.SUBMITTED)

        if simulate_lost_response:
            order.submission_unknown = True
            return BrokerSubmitResult(
                accepted=False,
                broker_order_id=broker_order_id,
                status=BrokerOrderStatus.SUBMITTED.value,
                submission_unknown=True,
            )

        fill_slices = self._plan_fills(request, assumptions, execution_at)
        return self._apply_fill_slices(order, fill_slices, execution_at)

    def recover_submission(self, client_order_id: str) -> BrokerSubmitResult | None:
        order = self._orders_by_client.get(client_order_id)
        if not order:
            return None
        order.submission_unknown = False
        return self._result_from_existing(order)

    def cancel_order(self, broker_order_id: str) -> bool:
        order = self._orders_by_id.get(broker_order_id)
        if not order:
            return False
        if order.status in (BrokerOrderStatus.FILLED, BrokerOrderStatus.CANCELLED, BrokerOrderStatus.REJECTED):
            return False
        if can_transition(order.status, BrokerOrderStatus.CANCELLED):
            order.status = BrokerOrderStatus.CANCELLED
            return True
        return False

    def replace_order(self, broker_order_id: str, **updates) -> bool:
        order = self._orders_by_id.get(broker_order_id)
        if not order or order.status not in (
            BrokerOrderStatus.SUBMITTED,
            BrokerOrderStatus.PARTIALLY_FILLED,
        ):
            return False
        # Architecture-ready — quantity amend only when unfilled remainder exists.
        new_qty = updates.get("quantity")
        if new_qty is not None:
            remaining = order.requested_quantity - order.filled_quantity
            if Decimal(str(new_qty)) > remaining:
                return False
        return True

    def sync_reconcile(
        self,
        *,
        local_cash: Decimal,
        broker_cash: Decimal,
        local_positions: dict,
        broker_positions: dict,
    ) -> ReconciliationReport:
        cash_delta = (broker_cash - local_cash).quantize(Decimal("0.0001"))
        pos_mismatch = 0
        for sym, bqty in broker_positions.items():
            lqty = local_positions.get(sym, Decimal("0"))
            if lqty != bqty:
                pos_mismatch += 1
        status = "ok"
        if abs(cash_delta) > Decimal("0.01") or pos_mismatch:
            status = "warning"
        if abs(cash_delta) > Decimal("100") or pos_mismatch > 2:
            status = "halted"
            self._halted = True
        return ReconciliationReport(
            status=status,
            cash_delta=cash_delta,
            position_mismatches=pos_mismatch,
            details={"local_positions": len(local_positions), "broker_positions": len(broker_positions)},
        )

    def _register(self, order: SimulatedBrokerOrder) -> None:
        self._orders_by_client[order.client_order_id] = order
        self._orders_by_id[order.broker_order_id] = order

    def _transition(self, order: SimulatedBrokerOrder, target: BrokerOrderStatus) -> None:
        if can_transition(order.status, target):
            order.status = target

    def _plan_fills(
        self,
        request: BrokerSubmitRequest,
        assumptions: ExecutionAssumptions,
        execution_at: datetime,
    ) -> list:
        direction = Direction.LONG if request.direction == "long" else Direction.SHORT
        side = "exit" if request.reduce_only else "entry"
        allow_partial = uses_realistic_broker(self.execution_model)
        return plan_deterministic_fills(
            client_order_id=request.client_order_id,
            execution_ts_iso=execution_at.isoformat(),
            direction=direction,
            side=side,
            total_quantity=request.quantity,
            base_price=request.mark_price,
            assumptions=assumptions,
            allow_partial=allow_partial,
        )

    def _apply_fill_slices(self, order: SimulatedBrokerOrder, slices, execution_at: datetime) -> BrokerSubmitResult:
        for sl in slices:
            order.fills.append((sl.sequence, sl.quantity, sl.fill))
            order.filled_quantity += sl.quantity
            if order.filled_quantity < order.requested_quantity:
                self._transition(order, BrokerOrderStatus.PARTIALLY_FILLED)
            else:
                self._transition(order, BrokerOrderStatus.FILLED)
                order.filled_at = execution_at
        return BrokerSubmitResult(
            accepted=True,
            broker_order_id=order.broker_order_id,
            status=order.status.value,
            fill_slices=slices,
        )

    def _result_from_existing(self, order: SimulatedBrokerOrder) -> BrokerSubmitResult:
        slices = []
        for seq, qty, fill in order.fills:
            from quantara_engine.broker.deterministic_fill_engine import SimulatedFillSlice

            slices.append(SimulatedFillSlice(sequence=seq, quantity=qty, fill=fill))
        return BrokerSubmitResult(
            accepted=order.status in (BrokerOrderStatus.FILLED, BrokerOrderStatus.PARTIALLY_FILLED),
            broker_order_id=order.broker_order_id,
            status=order.status.value,
            submission_unknown=order.submission_unknown,
            fill_slices=slices,
        )


def default_assumptions_for_product(product: ExecutionProduct | None) -> dict:
    return dict(EXECUTION_SIM_DEFAULTS)
