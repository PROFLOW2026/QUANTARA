"""Fixed IDs for paper risk competition experiments."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

OWNER_ID = "00000000-0000-0000-0000-000000000001"
LEGACY_PAPER_PORTFOLIO_ID = "00000000-0000-0000-0000-000000000010"
LEGACY_PAPER_INSTANCE_ID = "00000000-0000-0000-0000-000000000020"

# Archived 5-portfolio (1h-only) experiment — preserved for history.
LEGACY_COMPETITION_EXPERIMENT_ID = "00000000-0000-0000-0000-000000000100"
LEGACY_COMPETITION_TOTAL_INITIAL = Decimal("10000")

# Active 15-portfolio multi-timeframe experiment.
ACTIVE_COMPETITION_EXPERIMENT_ID = "00000000-0000-0000-0000-000000000200"
COMPETITION_EXPERIMENT_ID = ACTIVE_COMPETITION_EXPERIMENT_ID

COMPETITION_INITIAL_CAPITAL = Decimal("2000")
COMPETITION_TOTAL_INITIAL = Decimal("30000")

COMPETITION_NAME_HE = "השוואת 15 תיקים אוטומטיים"
COMPETITION_SUBTITLE_HE = "3 טווחי זמן × 5 רמות סיכון"
COMPETITION_DESCRIPTION = (
    "Fifteen isolated paper portfolios trading Gold Trend Pullback v1.0.0 on XAU/USD "
    "across 1h, 15m, and 5m completed candles with five risk-per-trade tiers each."
)

TIMEFRAME_ORDER: tuple[str, ...] = ("1h", "15m", "5m")

TIMEFRAME_HE: dict[str, str] = {
    "1h": "1 שעה",
    "15m": "15 דקות",
    "5m": "5 דקות",
}

TIMEFRAME_GROUP_TITLE_HE: dict[str, str] = {
    "1h": "מסחר לפי שעה",
    "15m": "מסחר לפי 15 דקות",
    "5m": "מסחר לפי 5 דקות",
}

RISK_TIERS: tuple[tuple[str, str], ...] = (
    ("very_conservative", "זהיר מאוד"),
    ("conservative", "שמרני"),
    ("balanced", "מאוזן"),
    ("aggressive", "אגרסивי"),
    ("very_aggressive", "אגרסיבי מאוד"),
)


@dataclass(frozen=True)
class CompetitionPortfolioDef:
    portfolio_id: str
    instance_id: str
    name_he: str
    risk_slug: str
    timeframe: str
    sort_order: int


def _build_portfolio_defs() -> tuple[CompetitionPortfolioDef, ...]:
    defs: list[CompetitionPortfolioDef] = []
    group_codes = {"1h": 11, "15m": 12, "5m": 13}
    tf_order = {"1h": 1, "15m": 2, "5m": 3}

    for timeframe in TIMEFRAME_ORDER:
        group = group_codes[timeframe]
        tf_he = TIMEFRAME_HE[timeframe]
        for risk_idx, (risk_slug, risk_he) in enumerate(RISK_TIERS, start=1):
            portfolio_suffix = group * 100 + risk_idx
            instance_suffix = (group + 10) * 100 + risk_idx
            portfolio_id = f"00000000-0000-0000-0000-{portfolio_suffix:012d}"
            instance_id = f"00000000-0000-0000-0000-{instance_suffix:012d}"
            sort_order = tf_order[timeframe] * 10 + risk_idx
            defs.append(
                CompetitionPortfolioDef(
                    portfolio_id=portfolio_id,
                    instance_id=instance_id,
                    name_he=f"{tf_he} — {risk_he}",
                    risk_slug=risk_slug,
                    timeframe=timeframe,
                    sort_order=sort_order,
                )
            )
    return tuple(defs)


ACTIVE_COMPETITION_PORTFOLIOS: tuple[CompetitionPortfolioDef, ...] = _build_portfolio_defs()

# Legacy 1h-only competition (archived, preserved in DB).
LEGACY_COMPETITION_PORTFOLIOS: tuple[CompetitionPortfolioDef, ...] = (
    CompetitionPortfolioDef(
        "00000000-0000-0000-0000-000000000101",
        "00000000-0000-0000-0000-000000000201",
        "זהיר מאוד",
        "very_conservative",
        "1h",
        1,
    ),
    CompetitionPortfolioDef(
        "00000000-0000-0000-0000-000000000102",
        "00000000-0000-0000-0000-000000000202",
        "שמרני",
        "conservative",
        "1h",
        2,
    ),
    CompetitionPortfolioDef(
        "00000000-0000-0000-0000-000000000103",
        "00000000-0000-0000-0000-000000000203",
        "מאוזן",
        "balanced",
        "1h",
        3,
    ),
    CompetitionPortfolioDef(
        "00000000-0000-0000-0000-000000000104",
        "00000000-0000-0000-0000-000000000204",
        "אגרסивי",
        "aggressive",
        "1h",
        4,
    ),
    CompetitionPortfolioDef(
        "00000000-0000-0000-0000-000000000105",
        "00000000-0000-0000-0000-000000000205",
        "אגרסивי מאוד",
        "very_aggressive",
        "1h",
        5,
    ),
)

# Backward-compatible alias for active experiment portfolios.
COMPETITION_PORTFOLIOS = ACTIVE_COMPETITION_PORTFOLIOS

RISK_SLUG_HE: dict[str, str] = {slug: he for slug, he in RISK_TIERS}

PORTFOLIO_DEF_BY_ID: dict[str, CompetitionPortfolioDef] = {
    p.portfolio_id: p
    for p in (*LEGACY_COMPETITION_PORTFOLIOS, *ACTIVE_COMPETITION_PORTFOLIOS)
}
