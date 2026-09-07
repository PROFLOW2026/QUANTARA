"""Tests that spread/slippage are not double-counted in P&L."""

from datetime import datetime, timezone
from decimal import Decimal

from quantara_engine.domain.types import Direction, ExecutionAssumptions, ExitReason
from quantara_engine.execution.fill_calculator import calculate_fill_price
from quantara_engine.portfolio.pnl import gross_pnl, net_pnl
from quantara_engine.domain.types import Mode, Portfolio
from quantara_engine.portfolio.service import PortfolioState


def test_balance_unchanged_on_entry():
    state = PortfolioState(
        portfolio=Portfolio(
            id="p1",
            name="Test",
            mode=Mode.PAPER,
            initial_capital=Decimal("10000"),
            balance=Decimal("10000"),
            equity=Decimal("10000"),
        )
    )
    balance_before = state.portfolio.balance
    assumptions = ExecutionAssumptions()
    fill = calculate_fill_price(
        Direction.LONG, "entry", Decimal("2650"), Decimal("1"), assumptions
    )
    from quantara_engine.domain.types import Order, OrderIntent, new_id

    intent = OrderIntent(
        id=new_id(),
        signal_id=new_id(),
        strategy_instance_id="si1",
        portfolio_id="p1",
        direction=Direction.LONG,
        quantity=Decimal("1"),
        stop_loss=Decimal("2640"),
        take_profit=Decimal("2670"),
        target_risk_amount=Decimal("100"),
        actual_risk_amount=Decimal("100"),
        signal_candle_timestamp=datetime.now(timezone.utc),
    )
    order = Order(
        id=new_id(),
        intent_id=intent.id,
        portfolio_id="p1",
        instrument_id="xauusd",
        direction=Direction.LONG,
        quantity=Decimal("1"),
    )
    state.open_position_from_fill(intent, fill, order, "v1", "xauusd", intent.signal_candle_timestamp)
    assert state.portfolio.balance == balance_before


def test_net_pnl_subtracts_fees_only_not_spread_twice():
    entry = calculate_fill_price(
        Direction.LONG, "entry", Decimal("2650"), Decimal("1"), ExecutionAssumptions()
    )
    exit_ = calculate_fill_price(
        Direction.LONG, "exit", Decimal("2660"), Decimal("1"), ExecutionAssumptions()
    )
    g = gross_pnl(Direction.LONG, entry.fill_price, exit_.fill_price, Decimal("1"))
    n = net_pnl(g, entry.fees, exit_.fees)
    # spread already in fill prices — net only subtracts explicit fees
    assert n == g - entry.fees - exit_.fees
    assert entry.spread_cost > 0
    assert exit_.spread_cost > 0
