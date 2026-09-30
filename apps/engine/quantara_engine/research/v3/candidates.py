"""Frozen V3 candidate universe — coarse parameter grids only."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from quantara_engine.strategies.registry import get_latest, validate_parameters


@dataclass(frozen=True)
class V3Candidate:
    candidate_id: str
    family: str
    strategy_slug: str
    version: str
    parameters: dict[str, Any]
    rationale: str


def _variants(
    slug: str,
    family: str,
    grids: list[dict[str, Any]],
    *,
    id_prefix: str | None = None,
) -> list[V3Candidate]:
    cls = get_latest(slug)
    version = cls.version()
    base = id_prefix or slug.replace("-", "_")
    out: list[V3Candidate] = []
    for i, overrides in enumerate(grids, start=1):
        params = validate_parameters(slug, version, overrides)
        cid = f"{base}_v{i}"
        out.append(
            V3Candidate(
                candidate_id=cid,
                family=family,
                strategy_slug=slug,
                version=version,
                parameters=params,
                rationale=f"Coarse variant {i} on frozen {slug} defaults",
            )
        )
    return out


def frozen_v3_candidates() -> list[V3Candidate]:
    """~48 definitions before asset/timeframe expansion."""
    candidates: list[V3Candidate] = []
    candidates += _variants(
        "gold-trend-pullback",
        "Trend Pullback",
        [
            {},
            {"atr_sl_multiplier": 2.0, "atr_tp_multiplier": 4.0},
            {"rsi_entry_min": 35, "rsi_entry_max": 65},
            {"ema_fast": 15, "ema_slow": 40},
            {"atr_sl_multiplier": 1.2, "atr_tp_multiplier": 2.5},
            {"ema_trend": 100, "rsi_entry_min": 40, "rsi_entry_max": 60},
            {"atr_sl_multiplier": 2.5, "atr_tp_multiplier": 3.5},
        ],
    )
    candidates += _variants(
        "opening-range-breakout",
        "Opening Range / Breakout",
        [
            {},
            {"min_candles_required": 30},
            {"min_candles_required": 20},
            {"min_candles_required": 40},
            {"opening_range_minutes": 15},
            {"opening_range_minutes": 45, "reward_risk_ratio": 2.5},
        ],
    )
    candidates += _variants(
        "mean-reversion",
        "Mean Reversion",
        [
            {},
            {"bb_period": 20, "bb_std": 2.5},
            {"rsi_oversold": 25, "rsi_overbought": 75},
            {"bb_period": 15, "bb_std": 2.0},
            {"rsi_oversold": 30, "rsi_overbought": 70},
        ],
    )
    candidates += _variants(
        "volatility-squeeze",
        "Volatility Breakout",
        [
            {},
            {"bb_width_lookback": 80},
            {"bb_width_lookback": 120},
            {"atr_sl_multiplier": 1.5, "atr_tp_multiplier": 3.0},
        ],
    )
    candidates += _variants(
        "momentum-continuation",
        "Momentum Continuation",
        [
            {},
            {"momentum_bars": 8},
            {"momentum_bars": 12},
            {"atr_sl_multiplier": 1.8, "atr_tp_multiplier": 3.5},
        ],
    )
    candidates += _variants(
        "gold-trend-pullback",
        "Simple trend/channel breakout",
        [
            {"atr_tp_multiplier": 5.0, "rsi_entry_min": 45, "rsi_entry_max": 55},
            {"ema_trend": 150, "atr_sl_multiplier": 1.2},
            {"ema_fast": 10, "ema_slow": 30, "atr_sl_multiplier": 1.5},
            {"atr_sl_multiplier": 2.5, "atr_tp_multiplier": 3.0},
        ],
        id_prefix="channel_breakout",
    )
    return candidates


def export_frozen_catalog() -> list[dict[str, Any]]:
    return [
        {
            "candidate_id": c.candidate_id,
            "family": c.family,
            "strategy_slug": c.strategy_slug,
            "parameters": c.parameters,
            "rationale": c.rationale,
        }
        for c in frozen_v3_candidates()
    ]
