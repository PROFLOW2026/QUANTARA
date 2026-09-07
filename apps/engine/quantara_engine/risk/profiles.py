"""Default risk profiles."""

from decimal import Decimal

from quantara_engine.domain.types import RiskProfile

DEFAULT_RISK_PROFILES: dict[str, RiskProfile] = {
    "very_conservative": RiskProfile(
        id="very_conservative",
        slug="very_conservative",
        name="Very Conservative",
        risk_per_trade_pct=Decimal("0.25"),
        max_open_positions=1,
        max_total_exposure_pct=Decimal("100"),
        daily_loss_limit_pct=Decimal("1.0"),
        max_drawdown_pct=Decimal("5"),
    ),
    "conservative": RiskProfile(
        id="conservative",
        slug="conservative",
        name="Conservative",
        risk_per_trade_pct=Decimal("0.5"),
        max_open_positions=1,
        max_total_exposure_pct=Decimal("50"),
        daily_loss_limit_pct=Decimal("1.5"),
        max_drawdown_pct=Decimal("5"),
    ),
    "balanced": RiskProfile(
        id="balanced",
        slug="balanced",
        name="Balanced",
        risk_per_trade_pct=Decimal("1.0"),
        max_open_positions=2,
        max_total_exposure_pct=Decimal("100"),
        daily_loss_limit_pct=Decimal("3.0"),
        max_drawdown_pct=Decimal("10"),
    ),
    "aggressive": RiskProfile(
        id="aggressive",
        slug="aggressive",
        name="Aggressive",
        risk_per_trade_pct=Decimal("1.5"),
        max_open_positions=3,
        max_total_exposure_pct=Decimal("100"),
        daily_loss_limit_pct=Decimal("5.0"),
        max_drawdown_pct=Decimal("15"),
    ),
    "very_aggressive": RiskProfile(
        id="very_aggressive",
        slug="very_aggressive",
        name="Very Aggressive",
        risk_per_trade_pct=Decimal("2.0"),
        max_open_positions=3,
        max_total_exposure_pct=Decimal("100"),
        daily_loss_limit_pct=Decimal("5.0"),
        max_drawdown_pct=Decimal("15"),
    ),
}


def get_risk_profile(slug: str) -> RiskProfile:
    return DEFAULT_RISK_PROFILES[slug]
