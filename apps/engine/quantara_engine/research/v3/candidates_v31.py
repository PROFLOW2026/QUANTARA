"""V3.1 bounded candidates — alternate families on existing strategy engines."""

from __future__ import annotations

from quantara_engine.research.v3.candidates import V3Candidate, _variants


def frozen_v31_candidates() -> list[V3Candidate]:
    """Different simple families (mapped to registered strategies), coarse grids only."""
    out: list[V3Candidate] = []
    out += _variants(
        "mean-reversion",
        "VWAP-style MR / channel",
        [
            {"rsi_oversold": 25, "rsi_overbought": 75},
            {"rsi_oversold": 30, "rsi_overbought": 70},
            {"bb_period": 30, "min_stretch_atr": 1.5},
            {"ema_period": 50, "atr_sl_multiplier": 1.8},
        ],
        id_prefix="v31_mr",
    )
    out += _variants(
        "momentum-continuation",
        "RS momentum / trend alignment",
        [
            {},
            {"momentum_bars": 8, "atr_sl_multiplier": 1.8},
            {"momentum_bars": 12, "adx_min": 22.0},
            {"ema_fast": 15, "ema_slow": 40, "momentum_bars": 6},
        ],
        id_prefix="v31_mom",
    )
    out += _variants(
        "volatility-squeeze",
        "Vol expansion breakout",
        [
            {},
            {"bb_width_percentile_max": 15.0, "atr_sl_multiplier": 1.2},
            {"compression_min_bars": 8, "expansion_atr_mult": 1.25},
            {"bb_std": 2.5, "reward_risk_ratio": 2.5},
        ],
        id_prefix="v31_vol",
    )
    out += _variants(
        "gold-trend-pullback",
        "ATR trend system",
        [
            {"ema_trend": 80, "atr_sl_multiplier": 1.5},
            {"ema_fast": 12, "ema_slow": 26, "atr_tp_multiplier": 3.0},
        ],
        id_prefix="v31_atr",
    )
    return out
