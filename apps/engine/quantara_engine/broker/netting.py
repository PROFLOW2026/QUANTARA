"""Netting position updates — sequential fill accounting."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from quantara_engine.broker.types import PositionMode


@dataclass(frozen=True)
class NettingFillResult:
    new_net_qty: Decimal
    new_avg_price: Decimal
    realized_pnl: Decimal
    closed_quantity: Decimal


def apply_fill_to_net_position(
    current_qty: Decimal,
    current_avg: Decimal,
    fill_qty: Decimal,
    fill_price: Decimal,
    direction: str,
    *,
    mode: PositionMode,
) -> tuple[Decimal, Decimal]:
    """Legacy wrapper — returns (new_net_qty, new_avg_price) only."""
    result = apply_fill_with_realized_pnl(
        current_qty, current_avg, fill_qty, fill_price, direction, mode=mode
    )
    return result.new_net_qty, result.new_avg_price


def apply_fill_with_realized_pnl(
    current_qty: Decimal,
    current_avg: Decimal,
    fill_qty: Decimal,
    fill_price: Decimal,
    direction: str,
    *,
    mode: PositionMode,
) -> NettingFillResult:
    """
    Apply a fill to broker net position with realized P&L on reductions.

    fill_qty is always positive; direction is long|short for the order side.
    """
    if mode != PositionMode.NETTING:
        raise NotImplementedError("Only NETTING mode is implemented at runtime")

    signed_fill = fill_qty if direction.lower() == "long" else -fill_qty
    realized = Decimal("0")
    closed_qty = Decimal("0")

    if current_qty == 0:
        return NettingFillResult(
            new_net_qty=signed_fill,
            new_avg_price=fill_price,
            realized_pnl=Decimal("0"),
            closed_quantity=Decimal("0"),
        )

    new_qty = current_qty + signed_fill

    same_direction = (current_qty > 0 and signed_fill > 0) or (current_qty < 0 and signed_fill < 0)

    if same_direction:
        total = abs(current_qty) * current_avg + fill_qty * fill_price
        new_avg = (total / abs(new_qty)).quantize(Decimal("0.00000001"))
        return NettingFillResult(
            new_net_qty=new_qty,
            new_avg_price=new_avg,
            realized_pnl=Decimal("0"),
            closed_quantity=Decimal("0"),
        )

    # Opposite direction — close partially, fully, or flip
    closed_qty = min(abs(signed_fill), abs(current_qty))
    if current_qty > 0:
        realized = ((fill_price - current_avg) * closed_qty).quantize(Decimal("0.01"))
    else:
        realized = ((current_avg - fill_price) * closed_qty).quantize(Decimal("0.01"))

    if abs(signed_fill) <= abs(current_qty):
        return NettingFillResult(
            new_net_qty=new_qty,
            new_avg_price=current_avg,
            realized_pnl=realized,
            closed_quantity=closed_qty,
        )

    # Flip through zero — remainder opens at fill price
    return NettingFillResult(
        new_net_qty=new_qty,
        new_avg_price=fill_price if new_qty != 0 else Decimal("0"),
        realized_pnl=realized,
        closed_quantity=closed_qty,
    )
