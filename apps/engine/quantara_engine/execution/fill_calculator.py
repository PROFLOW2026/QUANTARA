"""Fill price calculation with spread and slippage."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from quantara_engine.domain.types import Direction, ExecutionAssumptions


@dataclass
class FillResult:
    fill_price: Decimal
    base_price: Decimal
    spread_cost: Decimal
    slippage: Decimal
    fees: Decimal


def calculate_fill_price(
    direction: Direction,
    side: str,
    base_price: Decimal,
    quantity: Decimal,
    assumptions: ExecutionAssumptions,
) -> FillResult:
    half_spread = assumptions.spread / Decimal("2")
    if assumptions.slippage_per_side is not None:
        slippage_amount = assumptions.slippage_per_side.quantize(Decimal("0.00000001"))
    else:
        slippage_amount = (assumptions.slippage_pct * base_price).quantize(Decimal("0.00000001"))

    if side == "entry":
        if direction == Direction.LONG:
            fill_price = base_price + half_spread + slippage_amount
        else:
            fill_price = base_price - half_spread - slippage_amount
    else:
        if direction == Direction.LONG:
            fill_price = base_price - half_spread - slippage_amount
        else:
            fill_price = base_price + half_spread + slippage_amount

    fees = (quantity * fill_price * assumptions.fee_rate).quantize(Decimal("0.0001"))
    return FillResult(
        fill_price=fill_price.quantize(Decimal("0.00000001")),
        base_price=base_price,
        spread_cost=half_spread.quantize(Decimal("0.0001")),
        slippage=slippage_amount.quantize(Decimal("0.00000001")),
        fees=fees,
    )


def clamp_exit_base_price(
    base_price: Decimal,
    candle_low: Decimal,
    candle_high: Decimal,
    *,
    allow_outside_ohlc: bool = False,
) -> Decimal:
    """Clamp intrabar trigger prices to OHLC. Gap/open exits may lie outside the range."""
    if allow_outside_ohlc:
        return base_price
    return max(candle_low, min(candle_high, base_price))
