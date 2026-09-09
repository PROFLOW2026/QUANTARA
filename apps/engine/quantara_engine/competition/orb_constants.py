"""ORB paper competition — isolated from Robot A (Trend Pullback)."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from quantara_engine.competition.constants import OWNER_ID, RISK_TIERS

ORB_STRATEGY_SLUG = "opening-range-breakout"
ORB_STRATEGY_VERSION = "1.0.0"
ORB_COMPETITION_EXPERIMENT_ID = "00000000-0000-0000-0000-000000000300"
ORB_COMPETITION_INITIAL_CAPITAL = Decimal("2000")
ORB_COMPETITION_TOTAL_INITIAL = Decimal("50000")
ORB_TIMEFRAME = "5m"

ORB_ASSETS: tuple[str, ...] = ("SPY", "QQQ", "NVDA", "AAPL", "MSFT")

ORB_COMPETITION_NAME_HE = "ORB — 25 תיקי נייר"
ORB_COMPETITION_SUBTITLE_HE = "5 נכסים × 5 רמות סיכון"
ORB_COMPETITION_DESCRIPTION = (
    "Twenty-five isolated paper portfolios running Opening Range Breakout v1.0.0 "
    "on US equities (SPY, QQQ, NVDA, AAPL, MSFT) — 5m RTH only."
)

ORB_SETTINGS_ENABLED_KEY = "orb_competition_enabled"
ORB_SETTINGS_EXPERIMENT_KEY = "orb_competition_experiment_id"


@dataclass(frozen=True)
class OrbPortfolioDef:
    portfolio_id: str
    instance_id: str
    name_he: str
    risk_slug: str
    symbol: str
    timeframe: str
    sort_order: int


def _build_orb_portfolio_defs() -> tuple[OrbPortfolioDef, ...]:
    defs: list[OrbPortfolioDef] = []
    for asset_idx, symbol in enumerate(ORB_ASSETS, start=1):
        for risk_idx, (risk_slug, risk_he) in enumerate(RISK_TIERS, start=1):
            portfolio_suffix = 14000 + asset_idx * 100 + risk_idx
            instance_suffix = 15000 + asset_idx * 100 + risk_idx
            defs.append(
                OrbPortfolioDef(
                    portfolio_id=f"00000000-0000-0000-0000-{portfolio_suffix:012d}",
                    instance_id=f"00000000-0000-0000-0000-{instance_suffix:012d}",
                    name_he=f"ORB {symbol} — {risk_he}",
                    risk_slug=risk_slug,
                    symbol=symbol,
                    timeframe=ORB_TIMEFRAME,
                    sort_order=asset_idx * 10 + risk_idx,
                )
            )
    return tuple(defs)


ORB_COMPETITION_PORTFOLIOS: tuple[OrbPortfolioDef, ...] = _build_orb_portfolio_defs()

ORB_PORTFOLIO_DEF_BY_ID: dict[str, OrbPortfolioDef] = {
    p.portfolio_id: p for p in ORB_COMPETITION_PORTFOLIOS
}
