"""Robots C/D/E — 15m multi-strategy paper competition portfolios."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from quantara_engine.competition.constants import OWNER_ID, RISK_TIERS
from quantara_engine.market_data.active_universe import list_active_db_symbols

MULTI_STRATEGY_TIMEFRAME = "15m"
MULTI_STRATEGY_INITIAL_CAPITAL = Decimal("2000")
SHADOW_REFERENCE_TOTAL = Decimal("560000")

MEAN_REVERSION_STRATEGY_SLUG = "mean-reversion"
MEAN_REVERSION_STRATEGY_VERSION = "1.0.0"
MEAN_REVERSION_EXPERIMENT_ID = "00000000-0000-0000-0000-000000000500"
MEAN_REVERSION_SETTINGS_ENABLED_KEY = "mean_reversion_competition_enabled"

VOLATILITY_SQUEEZE_STRATEGY_SLUG = "volatility-squeeze"
VOLATILITY_SQUEEZE_STRATEGY_VERSION = "1.0.0"
VOLATILITY_SQUEEZE_EXPERIMENT_ID = "00000000-0000-0000-0000-000000000600"
VOLATILITY_SQUEEZE_SETTINGS_ENABLED_KEY = "volatility_squeeze_competition_enabled"

MOMENTUM_CONTINUATION_STRATEGY_SLUG = "momentum-continuation"
MOMENTUM_CONTINUATION_STRATEGY_VERSION = "1.0.0"
MOMENTUM_CONTINUATION_EXPERIMENT_ID = "00000000-0000-0000-0000-000000000700"
MOMENTUM_CONTINUATION_SETTINGS_ENABLED_KEY = "momentum_continuation_competition_enabled"

MULTI_STRATEGY_ASSETS: tuple[str, ...] = list_active_db_symbols()


@dataclass(frozen=True)
class MultiStrategyPortfolioDef:
    portfolio_id: str
    instance_id: str
    name_he: str
    risk_slug: str
    symbol: str
    timeframe: str
    strategy_slug: str
    experiment_id: str
    sort_order: int


def _build_defs(
    *,
    strategy_slug: str,
    experiment_id: str,
    name_prefix_he: str,
    portfolio_base: int,
    instance_base: int,
    sort_base: int,
) -> tuple[MultiStrategyPortfolioDef, ...]:
    defs: list[MultiStrategyPortfolioDef] = []
    for asset_idx, symbol in enumerate(MULTI_STRATEGY_ASSETS, start=1):
        for risk_idx, (risk_slug, risk_he) in enumerate(RISK_TIERS, start=1):
            portfolio_suffix = portfolio_base + asset_idx * 100 + risk_idx
            instance_suffix = instance_base + asset_idx * 100 + risk_idx
            defs.append(
                MultiStrategyPortfolioDef(
                    portfolio_id=f"00000000-0000-0000-0000-{portfolio_suffix:012d}",
                    instance_id=f"00000000-0000-0000-0000-{instance_suffix:012d}",
                    name_he=f"{name_prefix_he} {symbol} — {risk_he}",
                    risk_slug=risk_slug,
                    symbol=symbol,
                    timeframe=MULTI_STRATEGY_TIMEFRAME,
                    strategy_slug=strategy_slug,
                    experiment_id=experiment_id,
                    sort_order=sort_base + asset_idx * 10 + risk_idx,
                )
            )
    return tuple(defs)


MEAN_REVERSION_PORTFOLIOS: tuple[MultiStrategyPortfolioDef, ...] = _build_defs(
    strategy_slug=MEAN_REVERSION_STRATEGY_SLUG,
    experiment_id=MEAN_REVERSION_EXPERIMENT_ID,
    name_prefix_he="MR",
    portfolio_base=40000,
    instance_base=41000,
    sort_base=400,
)

VOLATILITY_SQUEEZE_PORTFOLIOS: tuple[MultiStrategyPortfolioDef, ...] = _build_defs(
    strategy_slug=VOLATILITY_SQUEEZE_STRATEGY_SLUG,
    experiment_id=VOLATILITY_SQUEEZE_EXPERIMENT_ID,
    name_prefix_he="SQZ",
    portfolio_base=42000,
    instance_base=43000,
    sort_base=500,
)

MOMENTUM_CONTINUATION_PORTFOLIOS: tuple[MultiStrategyPortfolioDef, ...] = _build_defs(
    strategy_slug=MOMENTUM_CONTINUATION_STRATEGY_SLUG,
    experiment_id=MOMENTUM_CONTINUATION_EXPERIMENT_ID,
    name_prefix_he="MOM",
    portfolio_base=44000,
    instance_base=45000,
    sort_base=600,
)

ALL_MULTI_STRATEGY_PORTFOLIOS: tuple[MultiStrategyPortfolioDef, ...] = (
    *MEAN_REVERSION_PORTFOLIOS,
    *VOLATILITY_SQUEEZE_PORTFOLIOS,
    *MOMENTUM_CONTINUATION_PORTFOLIOS,
)

PORTFOLIO_DEF_BY_ID: dict[str, MultiStrategyPortfolioDef] = {
    p.portfolio_id: p for p in ALL_MULTI_STRATEGY_PORTFOLIOS
}

EXPERIMENT_META: dict[str, dict[str, str]] = {
    MEAN_REVERSION_EXPERIMENT_ID: {
        "name_he": "רובוט C — חזרה לממוצע",
        "subtitle_he": "8 נכסים × 15 דקות × 5 רמות סיכון",
        "description": "Forty paper portfolios running Mean Reversion v1.0.0 on 15m candles.",
    },
    VOLATILITY_SQUEEZE_EXPERIMENT_ID: {
        "name_he": "רובוט D — התכווצות והתפרצות",
        "subtitle_he": "8 נכסים × 15 דקות × 5 רמות סיכון",
        "description": "Forty paper portfolios running Volatility Squeeze v1.0.0 on 15m candles.",
    },
    MOMENTUM_CONTINUATION_EXPERIMENT_ID: {
        "name_he": "רובוט E — מומנטום",
        "subtitle_he": "8 נכסים × 15 דקות × 5 רמות סיכון",
        "description": "Forty paper portfolios running Momentum Continuation v1.0.0 on 15m candles.",
    },
}

SETTINGS_BY_STRATEGY: dict[str, str] = {
    MEAN_REVERSION_STRATEGY_SLUG: MEAN_REVERSION_SETTINGS_ENABLED_KEY,
    VOLATILITY_SQUEEZE_STRATEGY_SLUG: VOLATILITY_SQUEEZE_SETTINGS_ENABLED_KEY,
    MOMENTUM_CONTINUATION_STRATEGY_SLUG: MOMENTUM_CONTINUATION_SETTINGS_ENABLED_KEY,
}
