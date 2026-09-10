"""Paper broker adapter."""

from __future__ import annotations

from decimal import Decimal

from quantara_engine.domain.types import (
    Direction,
    ExecutionAssumptions,
    Order,
    OrderIntent,
    OrderStatus,
    new_id,
)
from quantara_engine.execution.fill_calculator import (
    FillResult,
    calculate_fill_price,
    clamp_exit_base_price,
)


class PaperBrokerAdapter:
    def __init__(
        self,
        instrument_id: str,
        assumptions: ExecutionAssumptions | None = None,
    ) -> None:
        self.instrument_id = instrument_id
        self.assumptions = assumptions or ExecutionAssumptions()

    def execute_entry(self, intent: OrderIntent, candle) -> tuple[Order, FillResult]:
        base_price = candle.open
        if intent.is_close:
            pos_dir = Direction.LONG if intent.direction == Direction.SHORT else Direction.SHORT
            fill = calculate_fill_price(
                direction=pos_dir,
                side="exit",
                base_price=base_price,
                quantity=intent.quantity,
                assumptions=self.assumptions,
            )
        else:
            fill = calculate_fill_price(
                direction=intent.direction,
                side="entry",
                base_price=base_price,
                quantity=intent.quantity,
                assumptions=self.assumptions,
            )

        order = Order(
            id=new_id(),
            intent_id=intent.id,
            portfolio_id=intent.portfolio_id,
            instrument_id=self.instrument_id,
            direction=intent.direction,
            quantity=intent.quantity,
            status=OrderStatus.FILLED,
            submitted_at=candle.timestamp,
            broker_order_id=f"paper-{new_id()[:8]}",
        )
        return order, fill

    def execute_exit_at_trigger(
        self,
        position_direction: Direction,
        quantity: Decimal,
        candle,
        trigger_price: Decimal,
        portfolio_id: str = "",
        *,
        gap_exit: bool = False,
    ) -> tuple[Order, FillResult]:
        clamped = clamp_exit_base_price(
            trigger_price,
            candle.low,
            candle.high,
            allow_outside_ohlc=gap_exit,
        )
        fill = calculate_fill_price(
            direction=position_direction,
            side="exit",
            base_price=clamped,
            quantity=quantity,
            assumptions=self.assumptions,
        )
        order = Order(
            id=new_id(),
            intent_id="",
            portfolio_id=portfolio_id,
            instrument_id=self.instrument_id,
            direction=Direction.SHORT if position_direction == Direction.LONG else Direction.LONG,
            quantity=quantity,
            status=OrderStatus.FILLED,
            submitted_at=candle.timestamp,
        )
        return order, fill
