"""Broker adapter protocol."""

from __future__ import annotations

from decimal import Decimal
from typing import Protocol

from quantara_engine.domain.types import Candle, Order, OrderIntent
from quantara_engine.execution.fill_calculator import FillResult


class BrokerAdapter(Protocol):
    def execute_entry(
        self,
        intent: OrderIntent,
        candle: Candle,
    ) -> tuple[Order, FillResult]:
        ...

    def execute_exit(
        self,
        intent: OrderIntent,
        candle: Candle,
        base_price: Decimal,
    ) -> tuple[Order, FillResult]:
        ...
