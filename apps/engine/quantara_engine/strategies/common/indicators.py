"""Extended technical indicators for multi-strategy robots."""

from __future__ import annotations

from decimal import Decimal

import numpy as np
import pandas as pd

from quantara_engine.strategies.gold_trend_pullback.indicators import (
    atr,
    candles_to_df,
    ema,
    rsi,
    to_decimal,
)

__all__ = [
    "atr",
    "bollinger_bands",
    "candles_to_df",
    "ema",
    "ema_slope",
    "keltner_channels",
    "normalized_momentum",
    "percentile_rank",
    "rsi",
    "to_decimal",
    "wilder_adx",
]


def bollinger_bands(
    series: pd.Series, period: int = 20, std_mult: float = 2.0
) -> tuple[pd.Series, pd.Series, pd.Series]:
    middle = series.rolling(window=period, min_periods=period).mean()
    std = series.rolling(window=period, min_periods=period).std()
    upper = middle + std_mult * std
    lower = middle - std_mult * std
    return upper, middle, lower


def keltner_channels(
    df: pd.DataFrame, period: int = 20, atr_mult: float = 2.0, atr_period: int = 14
) -> tuple[pd.Series, pd.Series, pd.Series]:
    middle = ema(df["close"], period)
    atr_vals = atr(df, atr_period)
    upper = middle + atr_mult * atr_vals
    lower = middle - atr_mult * atr_vals
    return upper, middle, lower


def _directional_system(df: pd.DataFrame, period: int) -> tuple[pd.Series, pd.Series, pd.Series]:
    high = df["high"]
    low = df["low"]
    close = df["close"]
    prev_high = high.shift(1)
    prev_low = low.shift(1)
    prev_close = close.shift(1)

    up_move = high - prev_high
    down_move = prev_low - low
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

    tr = pd.concat(
        [
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)

    alpha = 1 / period
    tr_smooth = tr.ewm(alpha=alpha, adjust=False).mean()
    plus_di = 100 * pd.Series(plus_dm, index=df.index).ewm(alpha=alpha, adjust=False).mean() / tr_smooth
    minus_di = 100 * pd.Series(minus_dm, index=df.index).ewm(alpha=alpha, adjust=False).mean() / tr_smooth
    dx = ((plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)) * 100
    adx = dx.ewm(alpha=alpha, adjust=False).mean()
    return plus_di, minus_di, adx


def wilder_adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    return _directional_system(df, period)[2]


def bb_width_percentile(bb_width: pd.Series, lookback: int) -> pd.Series:
    def _pct(window: pd.Series) -> float:
        if len(window) < 2:
            return float("nan")
        current = window.iloc[-1]
        if pd.isna(current):
            return float("nan")
        return float((window <= current).sum() / len(window) * 100)

    return bb_width.rolling(window=lookback, min_periods=lookback).apply(_pct, raw=False)


def percentile_rank(series: pd.Series, lookback: int) -> pd.Series:
    return bb_width_percentile(series, lookback)


def ema_slope(series: pd.Series, lookback: int = 3) -> pd.Series:
    return series - series.shift(lookback)


def normalized_momentum(df: pd.DataFrame, bars: int, atr_period: int = 14) -> pd.Series:
    atr_vals = atr(df, atr_period)
    move = df["close"] - df["close"].shift(bars)
    return move / atr_vals.replace(0, np.nan)


def session_open_for_asset(db_symbol: str, ts) -> bool:
    from quantara_engine.market_data.registry import get_asset
    from quantara_engine.market_data.sessions import session_allows_entries

    asset = get_asset(db_symbol)
    if not asset:
        return True
    return session_allows_entries(asset.trading_sessions, ts)
