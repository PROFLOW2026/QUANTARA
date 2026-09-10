"""ORB paper competition — isolated from Robot A (Trend Pullback)."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from quantara_engine.competition.constants import OWNER_ID, RISK_TIERS
from quantara_engine.market_data.active_universe import list_active_db_symbols

ORB_STRATEGY_SLUG = "opening-range-breakout"
ORB_STRATEGY_VERSION = "1.0.0"
ORB_COMPETITION_EXPERIMENT_ID = "00000000-0000-0000-0000-000000000300"
ORB_COMPETITION_INITIAL_CAPITAL = Decimal("2000")
ORB_COMPETITION_TOTAL_INITIAL = Decimal("80000")
ORB_TIMEFRAME = "5m"

ORB_ASSETS: tuple[str, ...] = list_active_db_symbols()

ORB_COMPETITION_NAME_HE = "ORB — 40 תיקי נייר"
ORB_COMPETITION_SUBTITLE_HE = "8 נכסים × 5 רמות סיכון"
ORB_COMPETITION_DESCRIPTION = (
    "Forty isolated paper portfolios running Opening Range Breakout v1.0.0 "
    "on all eight active assets — US equities use RTH opening range; "
    "crypto/FX use UTC daily opening range."
)

ORB_SETTINGS_ENABLED_KEY = "orb_competition_enabled"
ORB_SETTINGS_EXPERIMENT_KEY = "orb_competition_experiment_id"

# Archived ORB portfolios (old 5-equity universe) — deactivated by seed.
ARCHIVED_ORB_ASSETS: tuple[str, ...] = ("SPY", "QQQ", "NVDA", "AAPL", "MSFT")


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
            portfolio_suffix = 20000 + asset_idx * 100 + risk_idx
            instance_suffix = 30000 + asset_idx * 100 + risk_idx
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

# Legacy ORB portfolio IDs (5 assets × 5 tiers) — preserved, deactivated by seed.
ARCHIVED_ORB_PORTFOLIO_DEFS: tuple[OrbPortfolioDef, ...] = tuple(
    OrbPortfolioDef(
        portfolio_id=f"00000000-0000-0000-0000-{14000 + asset_idx * 100 + risk_idx:012d}",
        instance_id=f"00000000-0000-0000-0000-{15000 + asset_idx * 100 + risk_idx:012d}",
        name_he=f"ORB {symbol} — {risk_he}",
        risk_slug=risk_slug,
        symbol=symbol,
        timeframe=ORB_TIMEFRAME,
        sort_order=asset_idx * 10 + risk_idx,
    )
    for asset_idx, symbol in enumerate(ARCHIVED_ORB_ASSETS, start=1)
    for risk_idx, (risk_slug, risk_he) in enumerate(RISK_TIERS, start=1)
)
