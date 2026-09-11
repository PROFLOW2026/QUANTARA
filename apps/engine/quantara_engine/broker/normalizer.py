"""Quantity normalization to instrument step/min rules."""

from __future__ import annotations

from decimal import Decimal, ROUND_DOWN

from quantara_engine.broker.types import InstrumentSpec


def normalize_quantity(quantity: Decimal, spec: InstrumentSpec) -> Decimal:
    """Round down to quantity_step; return 0 if below min."""
    if quantity <= 0:
        return Decimal("0")
    step = spec.quantity_step
    if step <= 0:
        return quantity
    normalized = (quantity / step).to_integral_value(rounding=ROUND_DOWN) * step
    if normalized < spec.min_quantity:
        return Decimal("0")
    return normalized


def validate_quantity(quantity: Decimal, spec: InstrumentSpec) -> str | None:
    if quantity <= 0:
        return "quantity must be positive"
    if quantity < spec.min_quantity:
        return f"below min_quantity {spec.min_quantity}"
    step = spec.quantity_step
    if step > 0 and (quantity % step) != 0:
        return f"invalid quantity step (step={step})"
    if not spec.fractional and quantity != quantity.to_integral_value(rounding=ROUND_DOWN):
        return "fractional not allowed"
    return None
