"""Portfolio balance invariants — ACCOUNT BALANCE model."""

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from quantara_engine.domain.types import (
    Direction,
    ExecutionAssumptions,
    ExitReason,
    Mode,
    Order,
    OrderIntent,
    Portfolio,
    Position,
    PositionStatus,
    new_id,
)
from quantara_engine.execution.fill_calculator import calculate_fill_price
from quantara_engine.portfolio.balance_reconciliation import (
    apply_canonical_balance,
    compute_canonical_balance,
    compute_canonical_equity,
)
from quantara_engine.portfolio.service import PortfolioState


def _state(initial: str = "10000") -> PortfolioState:
    return PortfolioState(
        portfolio=Portfolio(
            id="p1",
            name="Test",
            mode=Mode.PAPER,
            initial_capital=Decimal(initial),
            balance=Decimal(initial),
            equity=Decimal(initial),
        )
    )


def _open_long(state: PortfolioState, qty: str = "1", entry: str = "100") -> Position:
    assumptions = ExecutionAssumptions()
    fill = calculate_fill_price(Direction.LONG, "entry", Decimal(entry), Decimal(qty), assumptions)
    intent = OrderIntent(
        id=new_id(),
        signal_id=new_id(),
        strategy_instance_id="si1",
        portfolio_id=state.portfolio.id,
        direction=Direction.LONG,
        quantity=Decimal(qty),
        stop_loss=Decimal("90"),
        take_profit=Decimal("110"),
        target_risk_amount=Decimal("100"),
        actual_risk_amount=Decimal("100"),
        signal_candle_timestamp=datetime.now(timezone.utc),
    )
    order = Order(
        id=new_id(),
        intent_id=intent.id,
        portfolio_id=state.portfolio.id,
        instrument_id="btc",
        direction=Direction.LONG,
        quantity=Decimal(qty),
    )
    return state.open_position_from_fill(
        intent, fill, order, "v1", "btc", intent.signal_candle_timestamp
    )


def _close(state: PortfolioState, position: Position, exit_price: str, qty: str | None = None) -> None:
    q = Decimal(qty or str(position.quantity))
    fill = calculate_fill_price(
        position.direction, "exit", Decimal(exit_price), q, ExecutionAssumptions()
    )
    state.close_position(position, fill, ExitReason.SL, datetime.now(timezone.utc))


def test_long_full_close_balance_invariant():
    state = _state()
    pos = _open_long(state, entry="100")
    assert state.portfolio.balance == Decimal("10000")
    _close(state, pos, "105")
    assert state.portfolio.balance > Decimal("10000")
    state.recalculate_equity({})
    assert state.portfolio.equity == state.portfolio.balance


def test_short_full_close_balance_invariant():
    state = _state()
    assumptions = ExecutionAssumptions()
    fill = calculate_fill_price(Direction.SHORT, "entry", Decimal("100"), Decimal("1"), assumptions)
    intent = OrderIntent(
        id=new_id(),
        signal_id=new_id(),
        strategy_instance_id="si1",
        portfolio_id=state.portfolio.id,
        direction=Direction.SHORT,
        quantity=Decimal("1"),
        stop_loss=Decimal("110"),
        take_profit=Decimal("90"),
        target_risk_amount=Decimal("100"),
        actual_risk_amount=Decimal("100"),
        signal_candle_timestamp=datetime.now(timezone.utc),
    )
    order = Order(
        id=new_id(),
        intent_id=intent.id,
        portfolio_id=state.portfolio.id,
        instrument_id="btc",
        direction=Direction.SHORT,
        quantity=Decimal("1"),
    )
    pos = state.open_position_from_fill(
        intent, fill, order, "v1", "btc", intent.signal_candle_timestamp
    )
    exit_fill = calculate_fill_price(
        Direction.SHORT, "exit", Decimal("95"), Decimal("1"), ExecutionAssumptions()
    )
    state.close_position(pos, exit_fill, ExitReason.TP, datetime.now(timezone.utc))
    assert state.portfolio.balance > Decimal("10000")


def test_double_close_raises():
    state = _state()
    pos = _open_long(state)
    _close(state, pos, "105")
    with pytest.raises(ValueError, match="already closed"):
        _close(state, pos, "106")


def test_equity_equals_balance_plus_unrealized():
    state = _state()
    pos = _open_long(state, entry="100")
    state.recalculate_equity(Decimal("105"))
    assert state.portfolio.equity == state.portfolio.balance + pos.unrealized_pnl


def test_canonical_balance_from_trades():
    initial = Decimal("2000")
    realized = Decimal("-826.95")
    assert compute_canonical_balance(initial, realized) == Decimal("1173.05")


def test_global_reconciliation_identity():
    initial = Decimal("320000")
    realized = Decimal("-7632.21")
    unrealized = Decimal("7.87")
    balance = compute_canonical_balance(initial, realized)
    equity = compute_canonical_equity(balance, unrealized)
    assert equity - initial == realized + unrealized


def test_apply_canonical_balance_repairs_drift():
    portfolio = Portfolio(
        id="p1",
        name="Test",
        mode=Mode.PAPER,
        initial_capital=Decimal("2000"),
        balance=Decimal("2000"),
        equity=Decimal("2000"),
        unrealized_pnl=Decimal("0"),
    )
    apply_canonical_balance(portfolio, Decimal("-3.33"))
    assert portfolio.balance == Decimal("1996.67")
    assert portfolio.equity == Decimal("1996.67")
