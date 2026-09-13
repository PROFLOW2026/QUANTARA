"""Real-broker-ready adapter contract — implemented by SimulatedBrokerEngine today."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Protocol

from quantara_engine.broker.execution_product import ExecutionProduct
from quantara_engine.broker.types import BrokerAccountSnapshot, BrokerOrderDecision


@dataclass
class BrokerSubmitRequest:
    client_order_id: str
    account_slug: str
    symbol: str
    direction: str
    quantity: Decimal
    order_type: str = "market"
    time_in_force: str = "day"
    reduce_only: bool = False
    execution_product: ExecutionProduct | None = None
    mark_price: Decimal = Decimal("0")
    is_close: bool = False
    opportunity_key: str | None = None
    metadata: dict = field(default_factory=dict)


@dataclass
class BrokerSubmitResult:
    accepted: bool
    broker_order_id: str | None = None
    status: str = "created"
    submission_unknown: bool = False
    decision: BrokerOrderDecision | None = None
    fill_slices: list = field(default_factory=list)


@dataclass
class ReconciliationReport:
    status: str  # ok | warning | halted
    cash_delta: Decimal = Decimal("0")
    equity_delta: Decimal = Decimal("0")
    position_mismatches: int = 0
    order_mismatches: int = 0
    details: dict = field(default_factory=dict)


class RealBrokerAdapter(Protocol):
    """Contract future RealBrokerAdapter must implement."""

    def get_account(self) -> BrokerAccountSnapshot: ...

    def get_cash(self) -> Decimal: ...

    def get_equity(self) -> Decimal: ...

    def get_buying_power(self) -> Decimal: ...

    def get_margin_state(self) -> dict: ...

    def get_positions(self) -> dict: ...

    def get_position(self, symbol: str): ...

    def get_orders(self, *, open_only: bool = False) -> list: ...

    def get_order(self, broker_order_id: str): ...

    def get_order_by_client_id(self, client_order_id: str): ...

    def submit_order(self, request: BrokerSubmitRequest) -> BrokerSubmitResult: ...

    def cancel_order(self, broker_order_id: str) -> bool: ...

    def replace_order(self, broker_order_id: str, **updates) -> bool: ...

    def get_fills(self, broker_order_id: str) -> list: ...

    def get_instrument_capabilities(self, symbol: str) -> dict: ...

    def get_instrument_details(self, symbol: str) -> dict: ...

    def get_short_availability(self, symbol: str) -> dict: ...

    def get_market_status(self, symbol: str) -> dict: ...

    def sync_reconcile(self) -> ReconciliationReport: ...
