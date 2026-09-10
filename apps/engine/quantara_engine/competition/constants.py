"""Fixed IDs for paper risk competition experiments."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from quantara_engine.market_data.active_universe import list_active_db_symbols

OWNER_ID = "00000000-0000-0000-0000-000000000001"

# Archived 5-portfolio (1h-only) experiment — preserved for history.
LEGACY_COMPETITION_EXPERIMENT_ID = "00000000-0000-0000-0000-000000000100"
LEGACY_COMPETITION_TOTAL_INITIAL = Decimal("10000")

# Archived 15-portfolio XAU-only multi-timeframe experiment — preserved for history.
ARCHIVED_XAU_COMPETITION_EXPERIMENT_ID = "00000000-0000-0000-0000-000000000200"

# Active 120-portfolio multi-asset multi-timeframe experiment.
ACTIVE_COMPETITION_EXPERIMENT_ID = "00000000-0000-0000-0000-000000000400"
COMPETITION_EXPERIMENT_ID = ACTIVE_COMPETITION_EXPERIMENT_ID

COMPETITION_INITIAL_CAPITAL = Decimal("2000")
COMPETITION_TOTAL_INITIAL = Decimal("240000")

COMPETITION_NAME_HE = "השוואת 120 תיקים אוטומטיים"
COMPETITION_SUBTITLE_HE = "8 נכסים × 3 טווחי זמן × 5 רמות סיכון"
COMPETITION_DESCRIPTION = (
    "One hundred twenty isolated paper portfolios trading Gold Trend Pullback v1.0.0 "
    "across eight active assets (BTC, ETH, XAU, GBP/JPY, NVDA, TSLA, AMD, COIN) "
    "on 1h, 15m, and 5m completed candles with five risk-per-trade tiers each."
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
    ("aggressive", "אגרסיבי"),
    ("very_aggressive", "אגרסיבי מאוד"),
)

ASSET_DISPLAY_HE: dict[str, str] = {
    "BTCUSD": "BTC/USD",
    "ETHUSD": "ETH/USD",
    "XAUUSD": "XAU/USD",
    "GBPJPY": "GBP/JPY",
    "NVDA": "NVDA",
    "TSLA": "TSLA",
    "AMD": "AMD",
    "COIN": "COIN",
}


@dataclass(frozen=True)
class CompetitionPortfolioDef:
    portfolio_id: str
    instance_id: str
    name_he: str
    risk_slug: str
    timeframe: str
    symbol: str
    sort_order: int


def _build_portfolio_defs() -> tuple[CompetitionPortfolioDef, ...]:
    defs: list[CompetitionPortfolioDef] = []
    tf_order = {"1h": 1, "15m": 2, "5m": 3}

    for asset_idx, symbol in enumerate(list_active_db_symbols(), start=1):
        display = ASSET_DISPLAY_HE.get(symbol, symbol)
        for tf_idx, timeframe in enumerate(TIMEFRAME_ORDER, start=1):
            tf_he = TIMEFRAME_HE[timeframe]
            for risk_idx, (risk_slug, risk_he) in enumerate(RISK_TIERS, start=1):
                portfolio_suffix = 10000 + asset_idx * 1000 + tf_idx * 100 + risk_idx
                instance_suffix = 50000 + asset_idx * 1000 + tf_idx * 100 + risk_idx
                portfolio_id = f"00000000-0000-0000-0000-{portfolio_suffix:012d}"
                instance_id = f"00000000-0000-0000-0000-{instance_suffix:012d}"
                sort_order = asset_idx * 100 + tf_order[timeframe] * 10 + risk_idx
                defs.append(
                    CompetitionPortfolioDef(
                        portfolio_id=portfolio_id,
                        instance_id=instance_id,
                        name_he=f"{display} {tf_he} — {risk_he}",
                        risk_slug=risk_slug,
                        timeframe=timeframe,
                        symbol=symbol,
                        sort_order=sort_order,
                    )
                )
    return tuple(defs)


ACTIVE_COMPETITION_PORTFOLIOS: tuple[CompetitionPortfolioDef, ...] = _build_portfolio_defs()

# Legacy archived portfolios (preserved in DB, deactivated by seed).
LEGACY_COMPETITION_PORTFOLIOS: tuple[CompetitionPortfolioDef, ...] = (
    CompetitionPortfolioDef(
        "00000000-0000-0000-0000-000000000101",
        "00000000-0000-0000-0000-000000000201",
        "זהיר מאוד",
        "very_conservative",
        "1h",
        "XAUUSD",
        1,
    ),
    CompetitionPortfolioDef(
        "00000000-0000-0000-0000-000000000102",
        "00000000-0000-0000-0000-000000000202",
        "שמרני",
        "conservative",
        "1h",
        "XAUUSD",
        2,
    ),
    CompetitionPortfolioDef(
        "00000000-0000-0000-0000-000000000103",
        "00000000-0000-0000-0000-000000000203",
        "מאוזן",
        "balanced",
        "1h",
        "XAUUSD",
        3,
    ),
    CompetitionPortfolioDef(
        "00000000-0000-0000-0000-000000000104",
        "00000000-0000-0000-0000-000000000204",
        "אגרסивי",
        "aggressive",
        "1h",
        "XAUUSD",
        4,
    ),
    CompetitionPortfolioDef(
        "00000000-0000-0000-0000-000000000105",
        "00000000-0000-0000-0000-000000000205",
        "אגרסיבי מאוד",
        "very_aggressive",
        "1h",
        "XAUUSD",
        5,
    ),
)

ARCHIVED_XAU_COMPETITION_PORTFOLIOS: tuple[CompetitionPortfolioDef, ...] = tuple(
    CompetitionPortfolioDef(
        portfolio_id=f"00000000-0000-0000-0000-{group * 100 + risk:012d}",
        instance_id=f"00000000-0000-0000-0000-{(group + 10) * 100 + risk:012d}",
        name_he=f"{TIMEFRAME_HE[tf]} — {he}",
        risk_slug=slug,
        timeframe=tf,
        symbol="XAUUSD",
        sort_order=tf_order * 10 + risk,
    )
    for tf, group, tf_order in (("1h", 11, 1), ("15m", 12, 2), ("5m", 13, 3))
    for risk, (slug, he) in enumerate(RISK_TIERS, start=1)
)

COMPETITION_PORTFOLIOS = ACTIVE_COMPETITION_PORTFOLIOS

RISK_SLUG_HE: dict[str, str] = {slug: he for slug, he in RISK_TIERS}

PORTFOLIO_DEF_BY_ID: dict[str, CompetitionPortfolioDef] = {
    p.portfolio_id: p
    for p in (
        *LEGACY_COMPETITION_PORTFOLIOS,
        *ARCHIVED_XAU_COMPETITION_PORTFOLIOS,
        *ACTIVE_COMPETITION_PORTFOLIOS,
    )
}
