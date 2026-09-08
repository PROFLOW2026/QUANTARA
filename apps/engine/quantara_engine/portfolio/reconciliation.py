"""Portfolio financial reconciliation helpers for post-incident repair."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from quantara_engine.domain.types import Portfolio, PortfolioStatus, RiskProfile
from quantara_engine.portfolio.service import PortfolioSnapshot

_FLAT_TOLERANCE = Decimal("0.01")
_MAX_PLAUSIBLE_UNREALIZED_RATIO = Decimal("0.25")


@dataclass(frozen=True)
class PeakReconciliationResult:
    canonical_peak_equity: Decimal
    current_drawdown_pct: Decimal
    should_unhalt: bool
    unhalt_reason: str | None
    excluded_phantom_snapshots: int
    valid_snapshot_peaks: list[Decimal]


def snapshot_valid_for_peak(snap: PortfolioSnapshot, portfolio: Portfolio) -> bool:
    """Return True if snapshot equity may contribute to canonical peak_equity."""
    if snap.open_positions_count == 0:
        return (
            abs(snap.equity - snap.balance) <= _FLAT_TOLERANCE
            and abs(snap.unrealized_pnl) <= _FLAT_TOLERANCE
        )

    if snap.unrealized_pnl < 0:
        return True

    cap = (portfolio.balance * _MAX_PLAUSIBLE_UNREALIZED_RATIO).quantize(Decimal("0.01"))
    return snap.unrealized_pnl <= cap


def compute_canonical_peak_equity(
    portfolio: Portfolio,
    snapshots: list[PortfolioSnapshot],
) -> PeakReconciliationResult:
    """Recompute peak from valid history only."""
    valid_peaks: list[Decimal] = [portfolio.equity]
    excluded = 0
    for snap in snapshots:
        if snapshot_valid_for_peak(snap, portfolio):
            valid_peaks.append(snap.equity)
        else:
            excluded += 1

    canonical = max(valid_peaks).quantize(Decimal("0.01"))
    if canonical < portfolio.equity:
        canonical = portfolio.equity.quantize(Decimal("0.01"))

    drawdown = Decimal("0")
    if canonical > 0:
        drawdown = ((canonical - portfolio.equity) / canonical * Decimal("100")).quantize(
            Decimal("0.0001")
        )

    return PeakReconciliationResult(
        canonical_peak_equity=canonical,
        current_drawdown_pct=drawdown,
        should_unhalt=False,
        unhalt_reason=None,
        excluded_phantom_snapshots=excluded,
        valid_snapshot_peaks=valid_peaks,
    )


def evaluate_halt_after_peak_repair(
    portfolio: Portfolio,
    risk_profile: RiskProfile,
    canonical_peak: Decimal,
) -> tuple[bool, str | None]:
    """Return (should_unhalt, reason) when halt was caused by inflated peak only."""
    if portfolio.status != PortfolioStatus.HALTED:
        return False, None

    if canonical_peak <= 0:
        return False, None

    drawdown = (canonical_peak - portfolio.equity) / canonical_peak * Decimal("100")
    if drawdown >= risk_profile.max_drawdown_pct:
        return False, None

    if portfolio.peak_equity > canonical_peak and portfolio.peak_equity > portfolio.equity:
        return True, "cleared_phantom_peak_equity_drawdown_halt"

    return False, None


def reconcile_portfolio_peak_and_halt(
    portfolio: Portfolio,
    risk_profile: RiskProfile,
    snapshots: list[PortfolioSnapshot],
) -> tuple[Portfolio, PeakReconciliationResult]:
    """Apply canonical peak + optional unhalt. Does not mutate balance/equity/P&L."""
    peak_result = compute_canonical_peak_equity(portfolio, snapshots)
    canonical = peak_result.canonical_peak_equity
    should_unhalt, reason = evaluate_halt_after_peak_repair(portfolio, risk_profile, canonical)

    portfolio.peak_equity = canonical
    drawdown = Decimal("0")
    if canonical > 0:
        drawdown = ((canonical - portfolio.equity) / canonical * Decimal("100")).quantize(
            Decimal("0.0001")
        )

    if should_unhalt:
        portfolio.status = PortfolioStatus.ACTIVE
        portfolio.halt_reason = None

    return portfolio, PeakReconciliationResult(
        canonical_peak_equity=canonical,
        current_drawdown_pct=drawdown,
        should_unhalt=should_unhalt,
        unhalt_reason=reason,
        excluded_phantom_snapshots=peak_result.excluded_phantom_snapshots,
        valid_snapshot_peaks=peak_result.valid_snapshot_peaks,
    )
