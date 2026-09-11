"""FIFO lot attribution for strategy legs against broker net positions."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass
class AttributionLot:
    strategy_leg_id: str
    portfolio_id: str
    quantity: Decimal
    entry_price: Decimal
    remaining_qty: Decimal


def fifo_reduce_lots(
    lots: list[AttributionLot],
    reduce_qty: Decimal,
) -> tuple[list[AttributionLot], Decimal]:
    """
    Reduce quantity from lots FIFO. Returns (updated_lots, realized_pnl).
    """
    remaining = reduce_qty
    realized = Decimal("0")
    updated: list[AttributionLot] = []
    for lot in lots:
        if remaining <= 0:
            updated.append(lot)
            continue
        take = min(lot.remaining_qty, remaining)
        if take <= 0:
            updated.append(lot)
            continue
        # P&L attribution handled by caller with exit price
        lot.remaining_qty -= take
        remaining -= take
        if lot.remaining_qty > 0:
            updated.append(lot)
    return updated, realized
