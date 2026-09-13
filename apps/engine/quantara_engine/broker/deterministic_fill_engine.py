"""Deterministic fill simulation — reproducible partial/multiple fills."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from decimal import Decimal

from quantara_engine.execution.fill_calculator import FillResult, calculate_fill_price
from quantara_engine.domain.types import Direction, ExecutionAssumptions


@dataclass(frozen=True)
class SimulatedFillSlice:
    sequence: int
    quantity: Decimal
    fill: FillResult


def _deterministic_unit(client_order_id: str, execution_ts_iso: str, salt: str) -> Decimal:
    raw = f"{client_order_id}:{execution_ts_iso}:{salt}".encode("utf-8")
    digest = hashlib.sha256(raw).hexdigest()
    return Decimal(int(digest[:8], 16)) / Decimal(int("ffffffff", 16))


def plan_deterministic_fills(
    *,
    client_order_id: str,
    execution_ts_iso: str,
    direction: Direction,
    side: str,
    total_quantity: Decimal,
    base_price: Decimal,
    assumptions: ExecutionAssumptions,
    allow_partial: bool = True,
    min_partial_quantity: Decimal = Decimal("0.0001"),
) -> list[SimulatedFillSlice]:
    """Split order into 1–2 deterministic fill slices when partial fills enabled."""
    if total_quantity <= 0:
        return []

    unit = _deterministic_unit(client_order_id, execution_ts_iso, "partial")
    if not allow_partial or total_quantity < min_partial_quantity * 2:
        fill = calculate_fill_price(direction, side, base_price, total_quantity, assumptions)
        return [SimulatedFillSlice(sequence=1, quantity=total_quantity, fill=fill)]

    # Second slice between 35%–65% of total when unit > 0.35
    if unit < Decimal("0.35"):
        fill = calculate_fill_price(direction, side, base_price, total_quantity, assumptions)
        return [SimulatedFillSlice(sequence=1, quantity=total_quantity, fill=fill)]

    first_frac = (Decimal("0.35") + unit * Decimal("0.30")).quantize(Decimal("0.0001"))
    first_qty = (total_quantity * first_frac).quantize(Decimal("0.00000001"))
    if first_qty <= 0 or first_qty >= total_quantity:
        fill = calculate_fill_price(direction, side, base_price, total_quantity, assumptions)
        return [SimulatedFillSlice(sequence=1, quantity=total_quantity, fill=fill)]

    second_qty = total_quantity - first_qty
    slices: list[SimulatedFillSlice] = []
    for seq, qty in ((1, first_qty), (2, second_qty)):
        slices.append(
            SimulatedFillSlice(
                sequence=seq,
                quantity=qty,
                fill=calculate_fill_price(direction, side, base_price, qty, assumptions),
            )
        )
    return slices
