"""Deterministic market regime classifier — candle-close, no look-ahead."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from quantara_engine.strategies.common.indicators import (
    _directional_system,
    atr,
    bollinger_bands,
    candles_to_df,
    ema,
    ema_slope,
    normalized_momentum,
    percentile_rank,
)


class StructureRegime(str, Enum):
    TREND_UP = "TREND_UP"
    TREND_DOWN = "TREND_DOWN"
    RANGE = "RANGE"
    TRANSITION = "TRANSITION"
    UNKNOWN = "UNKNOWN"


class VolatilityRegime(str, Enum):
    COMPRESSED = "COMPRESSED"
    NORMAL = "NORMAL"
    EXPANDED = "EXPANDED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class RegimeSnapshot:
    structure_regime: StructureRegime
    volatility_regime: VolatilityRegime
    metrics: dict


def classify_regime(candles: list, *, min_candles: int = 60) -> RegimeSnapshot:
    if len(candles) < min_candles:
        return RegimeSnapshot(
            structure_regime=StructureRegime.UNKNOWN,
            volatility_regime=VolatilityRegime.UNKNOWN,
            metrics={"reason": "insufficient_data", "have": len(candles), "need": min_candles},
        )

    df = candles_to_df(candles)
    idx = len(df) - 1
    close = float(df["close"].iloc[idx])

    ema20 = ema(df["close"], 20)
    ema50 = ema(df["close"], 50)
    e20 = float(ema20.iloc[idx])
    e50 = float(ema50.iloc[idx])
    slope20 = float(ema_slope(ema20, 3).iloc[idx])
    slope50 = float(ema_slope(ema50, 3).iloc[idx])

    _, _, adx_vals = _directional_system(df, 14)
    adx_val = float(adx_vals.iloc[idx])

    mom = normalized_momentum(df, 5, 14)
    mom_val = float(mom.iloc[idx]) if not _is_nan(mom.iloc[idx]) else 0.0

    bb_upper, _, bb_lower = bollinger_bands(df["close"], 20, 2.0)
    bb_width = (bb_upper - bb_lower) / df["close"].replace(0, float("nan"))
    width_pct = percentile_rank(bb_width, 100)
    width_pct_val = float(width_pct.iloc[idx]) if not _is_nan(width_pct.iloc[idx]) else 50.0

    atr_vals = atr(df, 14)
    atr_val = float(atr_vals.iloc[idx])
    atr_pct = percentile_rank(atr_vals, 100)
    atr_pct_val = float(atr_pct.iloc[idx]) if not _is_nan(atr_pct.iloc[idx]) else 50.0

    structure = _classify_structure(
        close=close,
        e20=e20,
        e50=e50,
        slope20=slope20,
        slope50=slope50,
        adx=adx_val,
        momentum=mom_val,
    )
    volatility = _classify_volatility(width_pct_val=width_pct_val, atr_pct_val=atr_pct_val)

    return RegimeSnapshot(
        structure_regime=structure,
        volatility_regime=volatility,
        metrics={
            "close": close,
            "ema20": e20,
            "ema50": e50,
            "ema20_slope": slope20,
            "ema50_slope": slope50,
            "adx": adx_val,
            "momentum_atr": mom_val,
            "bb_width_percentile": width_pct_val,
            "atr_percentile": atr_pct_val,
            "atr": atr_val,
        },
    )


def _classify_structure(
    *,
    close: float,
    e20: float,
    e50: float,
    slope20: float,
    slope50: float,
    adx: float,
    momentum: float,
) -> StructureRegime:
    if adx >= 25 and close > e20 > e50 and slope20 > 0 and slope50 > 0 and momentum > 0.3:
        return StructureRegime.TREND_UP
    if adx >= 25 and close < e20 < e50 and slope20 < 0 and slope50 < 0 and momentum < -0.3:
        return StructureRegime.TREND_DOWN
    if adx <= 22:
        return StructureRegime.RANGE
    if (slope20 > 0 and slope50 < 0) or (slope20 < 0 and slope50 > 0):
        return StructureRegime.TRANSITION
    if adx >= 22:
        if close > e50 and momentum > 0:
            return StructureRegime.TREND_UP
        if close < e50 and momentum < 0:
            return StructureRegime.TREND_DOWN
    return StructureRegime.TRANSITION


def _classify_volatility(*, width_pct_val: float, atr_pct_val: float) -> VolatilityRegime:
    score = (width_pct_val + atr_pct_val) / 2
    if score <= 25:
        return VolatilityRegime.COMPRESSED
    if score >= 75:
        return VolatilityRegime.EXPANDED
    return VolatilityRegime.NORMAL


def _is_nan(value) -> bool:
    import math

    try:
        return math.isnan(float(value))
    except (TypeError, ValueError):
        return True
