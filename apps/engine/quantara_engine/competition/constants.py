"""Fixed IDs for the 5-portfolio paper risk competition."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

OWNER_ID = "00000000-0000-0000-0000-000000000001"
LEGACY_PAPER_PORTFOLIO_ID = "00000000-0000-0000-0000-000000000010"
LEGACY_PAPER_INSTANCE_ID = "00000000-0000-0000-0000-000000000020"

COMPETITION_EXPERIMENT_ID = "00000000-0000-0000-0000-000000000100"
COMPETITION_INITIAL_CAPITAL = Decimal("2000")
COMPETITION_TOTAL_INITIAL = Decimal("10000")

COMPETITION_NAME_HE = "השוואת 5 תיקים — רמות סיכון"
COMPETITION_DESCRIPTION = (
    "Five isolated paper portfolios trading Gold Trend Pullback v1.0.0 on XAU/USD 1h "
    "with identical signals and different risk-per-trade sizing."
)


@dataclass(frozen=True)
class CompetitionPortfolioDef:
    portfolio_id: str
    instance_id: str
    name_he: str
    risk_slug: str
    sort_order: int


COMPETITION_PORTFOLIOS: tuple[CompetitionPortfolioDef, ...] = (
    CompetitionPortfolioDef(
        "00000000-0000-0000-0000-000000000101",
        "00000000-0000-0000-0000-000000000201",
        "זהיר מאוד",
        "very_conservative",
        1,
    ),
    CompetitionPortfolioDef(
        "00000000-0000-0000-0000-000000000102",
        "00000000-0000-0000-0000-000000000202",
        "שמרני",
        "conservative",
        2,
    ),
    CompetitionPortfolioDef(
        "00000000-0000-0000-0000-000000000103",
        "00000000-0000-0000-0000-000000000203",
        "מאוזן",
        "balanced",
        3,
    ),
    CompetitionPortfolioDef(
        "00000000-0000-0000-0000-000000000104",
        "00000000-0000-0000-0000-000000000204",
        "אגרסивי",
        "aggressive",
        4,
    ),
    CompetitionPortfolioDef(
        "00000000-0000-0000-0000-000000000105",
        "00000000-0000-0000-0000-000000000205",
        "אגרסיבי מאוד",
        "very_aggressive",
        5,
    ),
)

RISK_SLUG_HE: dict[str, str] = {p.risk_slug: p.name_he for p in COMPETITION_PORTFOLIOS}
