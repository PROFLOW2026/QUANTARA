"""Regression tests for BTC mark-price incident (2026-09-08)."""

from datetime import datetime, timezone
from decimal import Decimal

from quantara_engine.domain.types import (
    Direction,
    Instrument,
    Mode,
    Portfolio,
    Position,
    PositionStatus,
)
from quantara_engine.portfolio.pnl import unrealized_pnl
from quantara_engine.portfolio.service import PortfolioState


def _btc_position(qty: str = "0.0296") -> Position:
    return Position(
        id="pos-btc",
        portfolio_id="p1",
        strategy_instance_id="si1",
        instrument_id="btc-id",
        direction=Direction.SHORT,
        quantity=Decimal(qty),
        entry_price=Decimal("78436.3395666"),
        current_price=Decimal("78436.3395666"),
        stop_loss=Decimal("78608.09801596"),
        take_profit=Decimal("78100.27896808"),
        status=PositionStatus.OPEN,
        opened_at=datetime(2026, 9, 8, 20, 35, tzinfo=timezone.utc),
        strategy_version_id="v1",
    )


def _xau_position() -> Position:
    return Position(
        id="pos-xau",
        portfolio_id="p1",
        strategy_instance_id="si2",
        instrument_id="xau-id",
        direction=Direction.LONG,
        quantity=Decimal("0.5"),
        entry_price=Decimal("2650"),
        current_price=Decimal("2650"),
        stop_loss=Decimal("2640"),
        take_profit=Decimal("2670"),
        status=PositionStatus.OPEN,
        opened_at=datetime(2026, 9, 8, 10, 0, tzinfo=timezone.utc),
        strategy_version_id="v1",
    )


def test_snapshot_xau_mark_must_not_mark_btc_positions():
    """Reproduce incident: XAU mark applied to BTC short inflates unrealized P&L."""
    entry = Decimal("78436.3395666")
    qty = Decimal("0.0296")
    xau_mark = Decimal("4390.88154")  # exact XAU 1h close from incident

    wrong = unrealized_pnl(Direction.SHORT, entry, xau_mark, qty)
    assert wrong == Decimal("2191.75")

    btc_mark = Decimal("78545.48")
    correct = unrealized_pnl(Direction.SHORT, entry, btc_mark, qty)
    assert correct < Decimal("0")
    assert correct == Decimal("-3.23")


def test_recalculate_equity_uses_instrument_scoped_marks():
    state = PortfolioState(
        portfolio=Portfolio(
            id="p1",
            name="Test",
            mode=Mode.PAPER,
            initial_capital=Decimal("2000"),
            balance=Decimal("2009.50"),
            equity=Decimal("2009.50"),
        ),
        positions=[_btc_position(), _xau_position()],
    )

    marks = {
        "btc-id": Decimal("78545.48"),
        "xau-id": Decimal("4390.88154"),
    }
    state.recalculate_equity(marks)

    btc = next(p for p in state.positions if p.instrument_id == "btc-id")
    xau = next(p for p in state.positions if p.instrument_id == "xau-id")
    assert btc.current_price == Decimal("78545.48")
    assert xau.current_price == Decimal("4390.88154")
    assert btc.unrealized_pnl == Decimal("-3.23")
    assert xau.unrealized_pnl == Decimal("870.44")
    assert state.portfolio.equity == state.portfolio.balance + btc.unrealized_pnl + xau.unrealized_pnl


def test_single_instrument_mark_dict_does_not_cross_contaminate():
    """Candle processor passes only its instrument mark — other positions keep prior mark."""
    state = PortfolioState(
        portfolio=Portfolio(
            id="p1",
            name="Test",
            mode=Mode.PAPER,
            initial_capital=Decimal("2000"),
            balance=Decimal("2009.50"),
            equity=Decimal("4201.25"),
        ),
        positions=[_btc_position()],
    )
    state.positions[0].current_price = Decimal("4390.88154")
    state.positions[0].unrealized_pnl = Decimal("2191.75")

    state.recalculate_equity({"btc-id": Decimal("78545.48")})
    assert state.positions[0].current_price == Decimal("78545.48")
    assert state.positions[0].unrealized_pnl == Decimal("-3.23")


def test_btc_position_sizing_risk_matches_tier():
    """Incident sizing was correct — guard against false positive on risk math."""
    eq_before = Decimal("2009.50")
    risk_pct = Decimal("0.25")
    expected = (eq_before * risk_pct / Decimal("100")).quantize(Decimal("0.01"))
    sl_dist = Decimal("171.75844936")
    qty = Decimal("0.0296")
    actual = (qty * sl_dist).quantize(Decimal("0.01"))
    assert expected == Decimal("5.02")
    assert actual == Decimal("5.08")
    assert abs(actual - expected) / expected < Decimal("0.02")
