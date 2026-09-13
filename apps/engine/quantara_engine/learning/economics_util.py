"""Helpers for observational learning (never mutate trading state)."""

from __future__ import annotations

from decimal import Decimal

from quantara_engine.domain.types import ExecutionAssumptions, Instrument
from quantara_engine.execution.cost_profile import execution_assumptions_for


def assumptions_for_symbol(symbol: str, reference_price: Decimal | None = None) -> ExecutionAssumptions:
    stub = Instrument(id="learning-shadow", symbol=symbol.upper(), name=symbol.upper())
    return execution_assumptions_for(stub, reference_price)
