"""Tests for portfolio peak_equity reconciliation after phantom P&L incidents."""

from decimal import Decimal

from quantara_engine.domain.types import Mode, Portfolio, PortfolioStatus, RiskProfile
from quantara_engine.portfolio.reconciliation import (
    evaluate_halt_after_peak_repair,
    reconcile_portfolio_peak_and_halt,
    snapshot_valid_for_peak,
)
from quantara_engine.portfolio.service import PortfolioSnapshot


def _portfolio(**kwargs) -> Portfolio:
    defaults = dict(
        id="p1",
        name="Test",
        mode=Mode.PAPER,
        initial_capital=Decimal("2000"),
        balance=Decimal("2009.50"),
        equity=Decimal("2009.50"),
        unrealized_pnl=Decimal("0"),
        peak_equity=Decimal("4201.25"),
        status=PortfolioStatus.HALTED,
    )
    defaults.update(kwargs)
    return Portfolio(**defaults)


def _risk_profile(max_dd: str = "5") -> RiskProfile:
    return RiskProfile(
        id="rp1",
        slug="very_conservative",
        name="Very Conservative",
        risk_per_trade_pct=Decimal("0.25"),
        max_open_positions=1,
        max_total_exposure_pct=Decimal("100"),
        daily_loss_limit_pct=Decimal("5"),
        max_drawdown_pct=Decimal(max_dd),
    )


def test_phantom_flat_snapshot_excluded():
    pf = _portfolio()
    valid = PortfolioSnapshot(
        portfolio_id="p1",
        timestamp=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
        balance=Decimal("2009.50"),
        equity=Decimal("2009.50"),
        exposure_notional=Decimal("0"),
        reserved_capital=Decimal("0"),
        unrealized_pnl=Decimal("0"),
        drawdown_pct=Decimal("0"),
        open_positions_count=0,
    )
    phantom = PortfolioSnapshot(
        portfolio_id="p1",
        timestamp=valid.timestamp,
        balance=Decimal("2009.50"),
        equity=Decimal("4201.25"),
        exposure_notional=Decimal("0"),
        reserved_capital=Decimal("0"),
        unrealized_pnl=Decimal("2191.75"),
        drawdown_pct=Decimal("0"),
        open_positions_count=0,
    )
    assert snapshot_valid_for_peak(valid, pf) is True
    assert snapshot_valid_for_peak(phantom, pf) is False


def test_reconcile_clears_phantom_halt():
    pf = _portfolio()
    rp = _risk_profile()
    valid_snap = PortfolioSnapshot(
        portfolio_id="p1",
        timestamp=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
        balance=Decimal("2009.50"),
        equity=Decimal("2009.50"),
        exposure_notional=Decimal("0"),
        reserved_capital=Decimal("0"),
        unrealized_pnl=Decimal("0"),
        drawdown_pct=Decimal("0"),
        open_positions_count=0,
    )
    repaired, result = reconcile_portfolio_peak_and_halt(pf, rp, [valid_snap])
    assert repaired.peak_equity == Decimal("2009.50")
    assert repaired.status == PortfolioStatus.ACTIVE
    assert result.should_unhalt is True
    assert float(result.current_drawdown_pct) == 0.0


def test_valid_drawdown_halt_not_cleared():
    pf = _portfolio(
        peak_equity=Decimal("2200"),
        equity=Decimal("2000"),
        status=PortfolioStatus.HALTED,
    )
    rp = _risk_profile("5")
    should, reason = evaluate_halt_after_peak_repair(pf, rp, Decimal("2200"))
    assert should is False
    assert reason is None
