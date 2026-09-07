"""Competition portfolio detection and sizing helpers."""

from __future__ import annotations

from decimal import Decimal

from quantara_engine.competition.constants import COMPETITION_PORTFOLIOS


COMPETITION_PORTFOLIO_IDS: frozenset[str] = frozenset(
    p.portfolio_id for p in COMPETITION_PORTFOLIOS
)


def is_competition_portfolio(portfolio_id: str) -> bool:
    return portfolio_id in COMPETITION_PORTFOLIO_IDS


def compute_sizing_metrics(
    quantity: Decimal,
    mark_price: Decimal,
    equity: Decimal,
    target_risk: Decimal,
    actual_risk: Decimal,
) -> dict[str, Decimal]:
    notional = (quantity * mark_price).quantize(Decimal("0.01"))
    exposure_pct = (
        (notional / equity * Decimal("100")).quantize(Decimal("0.01"))
        if equity > 0
        else Decimal("0")
    )
    leverage = (notional / equity).quantize(Decimal("0.01")) if equity > 0 else Decimal("0")
    target_risk_pct = (
        (target_risk / equity * Decimal("100")).quantize(Decimal("0.0001"))
        if equity > 0
        else Decimal("0")
    )
    actual_risk_pct = (
        (actual_risk / equity * Decimal("100")).quantize(Decimal("0.0001"))
        if equity > 0
        else Decimal("0")
    )
    return {
        "notional": notional,
        "exposure_pct": exposure_pct,
        "virtual_leverage": leverage,
        "target_risk_pct": target_risk_pct,
        "actual_risk_pct": actual_risk_pct,
    }
