"""Portfolio service — balance/equity model (no notional deduction on entry)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from quantara_engine.domain.types import (
    Direction,
    ExitReason,
    Fill,
    Order,
    OrderIntent,
    OrderStatus,
    Portfolio,
    Position,
    PositionStatus,
    Trade,
    new_id,
)
from quantara_engine.execution.fill_calculator import FillResult
from quantara_engine.portfolio.pnl import (
    gross_pnl,
    net_pnl,
    update_position_unrealized,
)


@dataclass
class PortfolioSnapshot:
    portfolio_id: str
    timestamp: datetime
    balance: Decimal
    equity: Decimal
    exposure_notional: Decimal
    reserved_capital: Decimal
    unrealized_pnl: Decimal
    drawdown_pct: Decimal
    open_positions_count: int


@dataclass
class PortfolioState:
    portfolio: Portfolio
    positions: list[Position] = field(default_factory=list)
    trades: list[Trade] = field(default_factory=list)
    snapshots: list[PortfolioSnapshot] = field(default_factory=list)

    def open_positions(self) -> list[Position]:
        return [p for p in self.positions if p.status == PositionStatus.OPEN]

    def _mark_for_position(
        self, position: Position, mark_price: Decimal | dict[str, Decimal]
    ) -> Decimal:
        if isinstance(mark_price, dict):
            return mark_price.get(position.instrument_id, position.current_price)
        return mark_price

    def recalculate_equity(self, mark_price: Decimal | dict[str, Decimal]) -> None:
        total_unrealized = Decimal("0")
        for pos in self.open_positions():
            mark = self._mark_for_position(pos, mark_price)
            total_unrealized += update_position_unrealized(pos, mark)
        self.portfolio.unrealized_pnl = total_unrealized.quantize(Decimal("0.01"))
        self.portfolio.equity = (self.portfolio.balance + self.portfolio.unrealized_pnl).quantize(
            Decimal("0.01")
        )
        exp = sum(
            (
                pos.quantity * self._mark_for_position(pos, mark_price)
                for pos in self.open_positions()
            ),
            Decimal("0"),
        ).quantize(Decimal("0.01"))
        self.portfolio.exposure_notional = exp
        self.portfolio.reserved_capital = exp
        if self.portfolio.equity > self.portfolio.peak_equity:
            self.portfolio.peak_equity = self.portfolio.equity

    def open_position_from_fill(
        self,
        intent: OrderIntent,
        fill: FillResult,
        order: Order,
        strategy_version_id: str,
        instrument_id: str,
        filled_at: datetime,
    ) -> Position:
        position = Position(
            id=new_id(),
            portfolio_id=self.portfolio.id,
            strategy_instance_id=intent.strategy_instance_id,
            instrument_id=instrument_id,
            direction=intent.direction,
            quantity=intent.quantity,
            entry_price=fill.fill_price,
            stop_loss=intent.stop_loss,
            take_profit=intent.take_profit,
            current_price=fill.fill_price,
            opened_at=filled_at,
            entry_fees=fill.fees,
            entry_slippage=fill.slippage,
            entry_spread=fill.spread_cost,
            target_risk_amount=intent.target_risk_amount,
            actual_risk_amount=intent.actual_risk_amount,
            strategy_version_id=strategy_version_id,
        )
        self.positions.append(position)
        # Balance unchanged on entry per canonical model
        return position

    def close_position(
        self,
        position: Position,
        fill: FillResult,
        exit_reason: ExitReason,
        closed_at: datetime,
    ) -> Trade:
        position.status = PositionStatus.CLOSED
        position.closed_at = closed_at
        g = gross_pnl(position.direction, position.entry_price, fill.fill_price, position.quantity)
        fees_total = position.entry_fees + fill.fees
        realized = net_pnl(g, position.entry_fees, fill.fees)
        duration = int((closed_at - (position.opened_at or closed_at)).total_seconds())

        trade = Trade(
            id=new_id(),
            position_id=position.id,
            portfolio_id=self.portfolio.id,
            strategy_instance_id=position.strategy_instance_id,
            strategy_version_id=position.strategy_version_id,
            instrument_id=position.instrument_id,
            direction=position.direction,
            quantity=position.quantity,
            entry_price=position.entry_price,
            exit_price=fill.fill_price,
            gross_pnl=g,
            realized_pnl=realized,
            fees_total=fees_total,
            slippage_total=position.entry_slippage + fill.slippage,
            spread_total=position.entry_spread + fill.spread_cost,
            target_risk_amount=position.target_risk_amount,
            actual_risk_amount=position.actual_risk_amount,
            exit_reason=exit_reason,
            duration_seconds=duration,
            opened_at=position.opened_at or closed_at,
            closed_at=closed_at,
        )
        self.trades.append(trade)
        self.portfolio.balance = (self.portfolio.balance + realized).quantize(Decimal("0.01"))
        position.unrealized_pnl = Decimal("0")
        return trade

    def create_snapshot(self, timestamp: datetime) -> PortfolioSnapshot:
        drawdown = Decimal("0")
        if self.portfolio.peak_equity > 0:
            drawdown = (
                (self.portfolio.peak_equity - self.portfolio.equity)
                / self.portfolio.peak_equity
                * Decimal("100")
            ).quantize(Decimal("0.0001"))
        snap = PortfolioSnapshot(
            portfolio_id=self.portfolio.id,
            timestamp=timestamp,
            balance=self.portfolio.balance,
            equity=self.portfolio.equity,
            exposure_notional=self.portfolio.exposure_notional,
            reserved_capital=self.portfolio.reserved_capital,
            unrealized_pnl=self.portfolio.unrealized_pnl,
            drawdown_pct=drawdown,
            open_positions_count=len(self.open_positions()),
        )
        self.snapshots.append(snap)
        return snap
