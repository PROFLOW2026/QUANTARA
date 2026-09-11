"""Netting and hedging position updates."""

from __future__ import annotations

from decimal import Decimal

from quantara_engine.broker.types import PositionMode


def apply_fill_to_net_position(
    current_qty: Decimal,
    current_avg: Decimal,
    fill_qty: Decimal,
    fill_price: Decimal,
    direction: str,
    *,
    mode: PositionMode,
) -> tuple[Decimal, Decimal]:
    """
    Apply a fill to broker net position.

    fill_qty is always positive; direction is long|short for the order side.
    Returns (new_net_qty, new_avg_price).
    """
    signed_fill = fill_qty if direction.lower() == "long" else -fill_qty

    if mode == PositionMode.HEDGING:
        # Hedging: track separate legs externally; net qty is algebraic sum
        new_qty = current_qty + signed_fill
        if new_qty == 0:
            return Decimal("0"), Decimal("0")
        if current_qty == 0 or (current_qty > 0 and signed_fill > 0) or (current_qty < 0 and signed_fill < 0):
            total_cost = abs(current_qty) * current_avg + fill_qty * fill_price
            new_avg = total_cost / abs(new_qty)
            return new_qty, new_avg.quantize(Decimal("0.00000001"))
        # Reducing position — avg unchanged on remainder
        return new_qty, current_avg

    # NETTING mode
    if current_qty == 0:
        return signed_fill, fill_price

    new_qty = current_qty + signed_fill

    # Same direction add — weighted average
    if (current_qty > 0 and signed_fill > 0) or (current_qty < 0 and signed_fill < 0):
        total = abs(current_qty) * current_avg + fill_qty * fill_price
        new_avg = total / abs(new_qty)
        return new_qty, new_avg.quantize(Decimal("0.00000001"))

    # Opposite direction — reduce or flip
    if abs(signed_fill) <= abs(current_qty):
        return new_qty, current_avg

    # Flip through zero — remainder at new price
    remainder = abs(new_qty)
    return new_qty, fill_price if remainder > 0 else Decimal("0")
