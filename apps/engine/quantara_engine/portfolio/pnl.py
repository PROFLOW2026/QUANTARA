"""P&L calculations — canonical accounting model."""

from __future__ import annotations

from decimal import Decimal

from quantara_engine.domain.types import Direction, Position


def unrealized_pnl(
    direction: Direction,
    entry_fill_price: Decimal,
    current_price: Decimal,
    quantity: Decimal,
) -> Decimal:
    if direction == Direction.LONG:
        return ((current_price - entry_fill_price) * quantity).quantize(Decimal("0.01"))
    return ((entry_fill_price - current_price) * quantity).quantize(Decimal("0.01"))


def gross_pnl(
    direction: Direction,
    entry_fill_price: Decimal,
    exit_fill_price: Decimal,
    quantity: Decimal,
) -> Decimal:
    if direction == Direction.LONG:
        return ((exit_fill_price - entry_fill_price) * quantity).quantize(Decimal("0.01"))
    return ((entry_fill_price - exit_fill_price) * quantity).quantize(Decimal("0.01"))


def net_pnl(gross: Decimal, entry_fees: Decimal, exit_fees: Decimal) -> Decimal:
    return (gross - entry_fees - exit_fees).quantize(Decimal("0.01"))


def update_position_unrealized(position: Position, mark_price: Decimal) -> Decimal:
    pnl = unrealized_pnl(position.direction, position.entry_price, mark_price, position.quantity)
    position.current_price = mark_price
    position.unrealized_pnl = pnl
    return pnl


def exposure_notional(positions: list[Position], mark_price: Decimal) -> Decimal:
    return sum((p.quantity * mark_price for p in positions), Decimal("0")).quantize(Decimal("0.01"))
