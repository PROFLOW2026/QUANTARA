"""Ledger-authoritative balance — idempotent exits and reconciliation."""

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from quantara_engine.domain.types import (
    Direction,
    ExecutionAssumptions,
    ExitReason,
    Mode,
    Portfolio,
    Position,
    PositionStatus,
    Trade,
    new_id,
)
from quantara_engine.execution.fill_calculator import calculate_fill_price
from quantara_engine.persistence.store import TradingStore
from quantara_engine.pipeline.candle_processor import CandleProcessor
from quantara_engine.portfolio.balance_reconciliation import (
    apply_canonical_balance,
    reconcile_portfolio_balance,
)
from quantara_engine.portfolio.service import PortfolioState


def _portfolio(initial: str = "2000") -> Portfolio:
    return Portfolio(
        id="p1",
        name="Test",
        mode=Mode.PAPER,
        initial_capital=Decimal(initial),
        balance=Decimal(initial),
        equity=Decimal(initial),
    )


def test_reconcile_zero_after_canonical_exit_persist():
    store = MagicMock(spec=TradingStore)
    store.trade_exists_for_position.return_value = False
    realized_map = {"p1": Decimal("45.00")}
    store.sum_realized_pnl_for_portfolio_ids.return_value = realized_map

    def _sync_from_ledger(portfolios, **kwargs):
        for p in portfolios:
            apply_canonical_balance(p, realized_map.get(p.id, Decimal("0")))

    store.sync_portfolios_balance_from_ledger.side_effect = _sync_from_ledger

    portfolio = _portfolio()
    portfolio.balance = Decimal("2000")
    state = PortfolioState(portfolio=portfolio)
    pos = Position(
        id="pos1",
        portfolio_id="p1",
        strategy_instance_id="si1",
        instrument_id="btc",
        direction=Direction.LONG,
        quantity=Decimal("1"),
        entry_price=Decimal("100"),
        stop_loss=Decimal("90"),
        take_profit=None,
        current_price=Decimal("105"),
        status=PositionStatus.OPEN,
    )
    state.positions.append(pos)
    fill = calculate_fill_price(Direction.LONG, "exit", Decimal("105"), Decimal("1"), ExecutionAssumptions())
    trade = state.close_position(pos, fill, ExitReason.TP, datetime.now(timezone.utc))

    processor = CandleProcessor(
        portfolio_state=state,
        strategy_instance=MagicMock(id="si1", strategy_version_id="v1"),
        instrument=MagicMock(id="btc", symbol="BTCUSD"),
        risk_profile=MagicMock(),
        broker=MagicMock(),
        store=store,
    )
    processor._persist_execution(None, MagicMock(id="o1"), fill, "exit", MagicMock(timestamp=datetime.now(timezone.utc)), pos, trade)

    store.sync_portfolios_balance_from_ledger.assert_called()
    result = reconcile_portfolio_balance(portfolio, Decimal("45.00"))
    assert result.balance_difference == Decimal("0.00")
    assert result.equity_difference == Decimal("0.00")


def test_double_exit_persist_skipped():
    store = MagicMock(spec=TradingStore)
    store.trade_exists_for_position.return_value = True
    portfolio = _portfolio()
    state = PortfolioState(portfolio=portfolio)
    processor = CandleProcessor(
        portfolio_state=state,
        strategy_instance=MagicMock(id="si1"),
        instrument=MagicMock(id="btc"),
        risk_profile=MagicMock(),
        broker=MagicMock(),
        store=store,
    )
    trade = Trade(
        id=new_id(),
        position_id="pos1",
        portfolio_id="p1",
        strategy_instance_id="si1",
        strategy_version_id="v1",
        instrument_id="btc",
        direction=Direction.LONG,
        quantity=Decimal("1"),
        entry_price=Decimal("100"),
        exit_price=Decimal("105"),
        gross_pnl=Decimal("5"),
        realized_pnl=Decimal("5"),
        fees_total=Decimal("0"),
        slippage_total=Decimal("0"),
        spread_total=Decimal("0"),
        target_risk_amount=Decimal("10"),
        actual_risk_amount=Decimal("10"),
        exit_reason=ExitReason.TP,
        duration_seconds=60,
        opened_at=datetime.now(timezone.utc),
        closed_at=datetime.now(timezone.utc),
    )
    processor._persist_execution(
        None, MagicMock(id="o1"), MagicMock(), "exit", MagicMock(timestamp=datetime.now(timezone.utc)), MagicMock(id="pos1"), trade
    )
    store.save_trade.assert_not_called()
