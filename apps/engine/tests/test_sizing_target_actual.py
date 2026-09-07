"""Tests for target vs actual risk sizing."""

from decimal import Decimal

from quantara_engine.domain.types import Instrument, Portfolio, Mode
from quantara_engine.risk.profiles import get_risk_profile
from quantara_engine.risk.sizing import (
    compute_actual_risk_amount,
    compute_position_size,
    compute_target_risk_amount,
)


def test_target_risk_amount_from_equity():
    portfolio = Portfolio(
        id="p1",
        name="Test",
        mode=Mode.PAPER,
        initial_capital=Decimal("10000"),
        balance=Decimal("10000"),
        equity=Decimal("10000"),
    )
    profile = get_risk_profile("balanced")
    target = compute_target_risk_amount(portfolio, profile)
    assert target == Decimal("100.00")


def test_actual_risk_after_exposure_cap():
    portfolio = Portfolio(
        id="p1",
        name="Test",
        mode=Mode.PAPER,
        initial_capital=Decimal("10000"),
        balance=Decimal("10000"),
        equity=Decimal("10000"),
        exposure_notional=Decimal("9500"),
        reserved_capital=Decimal("9500"),
    )
    instrument = Instrument(
        id="xauusd",
        symbol="XAUUSD",
        name="Gold",
        min_quantity=Decimal("0.01"),
        quantity_step=Decimal("0.01"),
    )
    profile = get_risk_profile("balanced")
    qty, target, actual, deny = compute_position_size(
        portfolio=portfolio,
        risk_profile=profile,
        instrument=instrument,
        entry_reference=Decimal("2650"),
        stop_loss=Decimal("2640"),
        direction="long",
        open_positions=[],
        mark_price=Decimal("2650"),
    )
    assert target == Decimal("100.00")
    assert deny is not None or actual <= target
    if qty > 0:
        sl_dist = Decimal("10")
        assert compute_actual_risk_amount(qty, sl_dist) == actual
