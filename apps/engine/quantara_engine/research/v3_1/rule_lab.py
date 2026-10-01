"""Simple rule-based bar simulation for V3.1 Stage A (no registry strategies)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

import numpy as np
import pandas as pd

from quantara_engine.strategies.common.indicators import atr, bollinger_bands, candles_to_df, ema, rsi


@dataclass
class SimTrade:
    closed_at: datetime
    direction: str
    realized_pnl: float
    risk_amount: float


def _simulate(
    df: pd.DataFrame,
    *,
    direction: str,
    entry_mask: pd.Series,
    atr_sl: float,
    atr_tp: float,
    max_bars: int = 24,
    cost_bps: float = 8.0,
) -> list[SimTrade]:
    trades: list[SimTrade] = []
    atr_s = atr(df, 14)
    i = 0
    n = len(df)
    while i < n - 1:
        if not bool(entry_mask.iloc[i]) or pd.isna(atr_s.iloc[i]) or atr_s.iloc[i] <= 0:
            i += 1
            continue
        entry = float(df["close"].iloc[i])
        risk_dist = float(atr_s.iloc[i]) * atr_sl
        if risk_dist <= 0:
            i += 1
            continue
        risk_amt = 100.0
        tp_dist = float(atr_s.iloc[i]) * atr_tp
        entry_cost = entry * (cost_bps / 10000.0)
        j = i + 1
        exit_px = entry
        closed_at = df.index[j] if hasattr(df.index[j], "to_pydatetime") else df.index[j]
        hit = False
        while j < min(n, i + 1 + max_bars):
            hi = float(df["high"].iloc[j])
            lo = float(df["low"].iloc[j])
            ts = df.index[j]
            if direction == "long":
                if lo <= entry - risk_dist:
                    exit_px = entry - risk_dist
                    closed_at = ts
                    hit = True
                    break
                if hi >= entry + tp_dist:
                    exit_px = entry + tp_dist
                    closed_at = ts
                    hit = True
                    break
            else:
                if hi >= entry + risk_dist:
                    exit_px = entry + risk_dist
                    closed_at = ts
                    hit = True
                    break
                if lo <= entry - tp_dist:
                    exit_px = entry - tp_dist
                    closed_at = ts
                    hit = True
                    break
            j += 1
        if not hit:
            exit_px = float(df["close"].iloc[j - 1])
            closed_at = df.index[j - 1]
        exit_cost = exit_px * (cost_bps / 10000.0)
        if direction == "long":
            pnl = (exit_px - entry) * (risk_amt / risk_dist) - entry_cost - exit_cost
        else:
            pnl = (entry - exit_px) * (risk_amt / risk_dist) - entry_cost - exit_cost
        if hasattr(closed_at, "to_pydatetime"):
            closed_at = closed_at.to_pydatetime()
        trades.append(SimTrade(closed_at=closed_at, direction=direction, realized_pnl=pnl, risk_amount=risk_amt))
        i = j
    return trades


def _df(candles: list) -> pd.DataFrame:
    df = candles_to_df(candles)
    df.index = pd.to_datetime([c.timestamp for c in candles], utc=True)
    return df


def entry_mask_series(
    family: str,
    df: pd.DataFrame,
    *,
    direction: str,
    parameters: dict[str, Any],
    session_open: Callable[[datetime], bool] | None = None,
) -> pd.Series:
    """Boolean entry mask on each bar (rule-lab semantics on that bar's close)."""
    close = df["close"]
    high = df["high"]
    low = df["low"]
    if family == "donchian_channel":
        n = int(parameters.get("channel", 20))
        upper = high.rolling(n).max().shift(1)
        lower = low.rolling(n).min().shift(1)
        return (close > upper) if direction == "long" else (close < lower)
    if family == "atr_trend":
        fast = int(parameters.get("ema_fast", 20))
        slow = int(parameters.get("ema_slow", 50))
        ef = ema(close, fast)
        es = ema(close, slow)
        return (ef > es) & (close > ef) if direction == "long" else (ef < es) & (close < ef)
    if family == "session_breakout":
        rng_hi = high.rolling(12).max().shift(1)
        rng_lo = low.rolling(12).min().shift(1)
        if session_open is not None:
            rth = pd.Series([session_open(ts.to_pydatetime()) for ts in df.index], index=df.index)
        else:
            hour = df.index.hour
            rth = (hour >= 13) & (hour <= 20)
        return (rth & (close > rng_hi)) if direction == "long" else (rth & (close < rng_lo))
    if family == "vwap_mean_reversion":
        vol = df["volume"] if "volume" in df.columns else pd.Series(1.0, index=df.index)
        vwap_proxy = (close * vol.replace(0, np.nan)).rolling(30).sum() / vol.rolling(30).sum().replace(0, np.nan)
        vwap_proxy = vwap_proxy.fillna(close.rolling(30).mean())
        dev = (close - vwap_proxy) / close
        thr = float(parameters.get("dev", 0.004))
        return (dev < -thr) if direction == "long" else (dev > thr)
    if family == "trend_pullback_alt":
        slow = int(parameters.get("ema_slow", 60))
        es = ema(close, slow)
        r = rsi(close, 14)
        pull = float(parameters.get("rsi_pull", 42))
        return ((close > es) & (r < pull)) if direction == "long" else ((close < es) & (r > (100 - pull)))
    # fallback: run single-bar backtest path not needed for current robust set
    return pd.Series(False, index=df.index)


def backtest_family(
    family: str,
    *,
    candles: list,
    direction: str,
    parameters: dict[str, Any],
) -> list[SimTrade]:
    df = _df(candles)
    if len(df) < 120:
        return []
    close = df["close"]
    high = df["high"]
    low = df["low"]
    atr_sl = float(parameters.get("atr_sl", 1.5))
    atr_tp = float(parameters.get("atr_tp", 2.5))
    max_bars = int(parameters.get("max_bars", 24))

    if family == "donchian_channel":
        n = int(parameters.get("channel", 20))
        upper = high.rolling(n).max().shift(1)
        lower = low.rolling(n).min().shift(1)
        if direction == "long":
            mask = close > upper
        else:
            mask = close < lower
        return _simulate(df, direction=direction, entry_mask=mask.fillna(False), atr_sl=atr_sl, atr_tp=atr_tp, max_bars=max_bars)

    if family == "atr_trend":
        fast = int(parameters.get("ema_fast", 20))
        slow = int(parameters.get("ema_slow", 50))
        ef = ema(close, fast)
        es = ema(close, slow)
        if direction == "long":
            mask = (ef > es) & (close > ef)
        else:
            mask = (ef < es) & (close < ef)
        return _simulate(df, direction=direction, entry_mask=mask.fillna(False), atr_sl=atr_sl, atr_tp=atr_tp, max_bars=max_bars)

    if family == "vol_expansion":
        bb_u, bb_m, bb_l = bollinger_bands(close, 20, float(parameters.get("bb_std", 2.0)))
        width = (bb_u - bb_l) / close.replace(0, np.nan)
        tight = width < width.rolling(100).quantile(0.2)
        if direction == "long":
            mask = tight.shift(1).fillna(False) & (close > bb_u.shift(1))
        else:
            mask = tight.shift(1).fillna(False) & (close < bb_l.shift(1))
        return _simulate(df, direction=direction, entry_mask=mask.fillna(False), atr_sl=atr_sl, atr_tp=atr_tp, max_bars=max_bars)

    if family == "mtf_trend":
        slow = int(parameters.get("ema_slow", 50))
        es = ema(close, slow)
        slope = es.diff(3)
        if direction == "long":
            mask = (close > es) & (slope > 0)
        else:
            mask = (close < es) & (slope < 0)
        return _simulate(df, direction=direction, entry_mask=mask.fillna(False), atr_sl=atr_sl, atr_tp=atr_tp, max_bars=max_bars)

    if family == "session_breakout":
        # US equities: break prior 12-bar range during RTH hours (UTC proxy 13–20)
        hour = df.index.hour
        rth = (hour >= 13) & (hour <= 20)
        rng_hi = high.rolling(12).max().shift(1)
        rng_lo = low.rolling(12).min().shift(1)
        if direction == "long":
            mask = rth & (close > rng_hi)
        else:
            mask = rth & (close < rng_lo)
        return _simulate(df, direction=direction, entry_mask=mask.fillna(False), atr_sl=atr_sl, atr_tp=atr_tp, max_bars=max_bars)

    if family == "vwap_mean_reversion":
        vol = df["volume"] if "volume" in df.columns else pd.Series(1.0, index=df.index)
        vwap_proxy = (close * vol.replace(0, np.nan)).rolling(30).sum() / vol.rolling(30).sum().replace(0, np.nan)
        vwap_proxy = vwap_proxy.fillna(close.rolling(30).mean())
        dev = (close - vwap_proxy) / close
        if direction == "long":
            mask = dev < -float(parameters.get("dev", 0.004))
        else:
            mask = dev > float(parameters.get("dev", 0.004))
        return _simulate(df, direction=direction, entry_mask=mask.fillna(False), atr_sl=atr_sl, atr_tp=atr_tp, max_bars=max_bars)

    if family == "relative_strength":
        mom = close.pct_change(int(parameters.get("mom_bars", 10)))
        if direction == "long":
            mask = mom > float(parameters.get("mom_min", 0.01))
        else:
            mask = mom < -float(parameters.get("mom_min", 0.01))
        return _simulate(df, direction=direction, entry_mask=mask.fillna(False), atr_sl=atr_sl, atr_tp=atr_tp, max_bars=max_bars)

    if family == "momentum_vol_filter":
        mom = close.pct_change(5)
        atr_s = atr(df, 14) / close
        cap = float(parameters.get("atr_cap", 0.02))
        if direction == "long":
            mask = (mom > 0.005) & (atr_s < cap)
        else:
            mask = (mom < -0.005) & (atr_s < cap)
        return _simulate(df, direction=direction, entry_mask=mask.fillna(False), atr_sl=atr_sl, atr_tp=atr_tp, max_bars=max_bars)

    if family == "range_atr_breakout":
        n = int(parameters.get("range", 15))
        rng = high.rolling(n).max() - low.rolling(n).min()
        atr_s = atr(df, 14)
        confirm = rng > atr_s * float(parameters.get("atr_mult", 1.2))
        upper = high.rolling(n).max().shift(1)
        lower = low.rolling(n).min().shift(1)
        if direction == "long":
            mask = confirm & (close > upper)
        else:
            mask = confirm & (close < lower)
        return _simulate(df, direction=direction, entry_mask=mask.fillna(False), atr_sl=atr_sl, atr_tp=atr_tp, max_bars=max_bars)

    if family == "trend_pullback_alt":
        slow = int(parameters.get("ema_slow", 60))
        es = ema(close, slow)
        r = rsi(close, 14)
        if direction == "long":
            mask = (close > es) & (r < float(parameters.get("rsi_pull", 42)))
        else:
            mask = (close < es) & (r > float(parameters.get("rsi_pull", 58)))
        return _simulate(df, direction=direction, entry_mask=mask.fillna(False), atr_sl=atr_sl, atr_tp=atr_tp, max_bars=max_bars)

    return []
