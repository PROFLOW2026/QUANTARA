"""V3.2 rule families — closed-bar signal, N+1 open entry, spread-aware costs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

import numpy as np
import pandas as pd

from quantara_engine.strategies.common.indicators import atr, bollinger_bands, candles_to_df, ema, rsi

SUPPORTED_FAMILIES: frozenset[str] = frozenset(
    {
        "donchian_breakout_v2",
        "atr_trailing_trend",
        "ema_pullback_continue",
        "vol_expansion_v2",
        "squeeze_release",
        "vwap_session_revert",
        "orb_continuation",
        "prev_day_hl_break",
        "mtf_trend_ltf_entry",
        "rsi_divergence_mr",
        "momentum_after_base",
        "channel_mean_revert",
    }
)


@dataclass
class SimTrade:
    closed_at: datetime
    direction: str
    realized_pnl: float
    risk_amount: float


def _df(candles: list) -> pd.DataFrame:
    df = candles_to_df(candles)
    df.index = pd.to_datetime([c.timestamp for c in candles], utc=True)
    return df


def _simulate_n1(
    df: pd.DataFrame,
    *,
    direction: str,
    entry_mask: pd.Series,
    atr_sl: float,
    atr_tp: float,
    max_bars: int = 24,
    cost_bps: float = 12.0,
) -> list[SimTrade]:
    """Signal on bar i close; enter at bar i+1 open; conservative SL/TP on bar OHLC."""
    trades: list[SimTrade] = []
    atr_s = atr(df, 14)
    n = len(df)
    i = 0
    while i < n - 2:
        if not bool(entry_mask.iloc[i]) or pd.isna(atr_s.iloc[i]) or atr_s.iloc[i] <= 0:
            i += 1
            continue
        entry = float(df["open"].iloc[i + 1])
        risk_dist = float(atr_s.iloc[i]) * atr_sl
        if risk_dist <= 0:
            i += 1
            continue
        risk_amt = 25.0  # 0.25% of 10k reference
        tp_dist = float(atr_s.iloc[i]) * atr_tp
        cost = entry * (cost_bps / 10000.0) * 2
        j = i + 2
        exit_px = entry
        closed_at = df.index[j - 1]
        hit = False
        while j < min(n, i + 2 + max_bars):
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
        units = risk_amt / risk_dist
        pnl = (exit_px - entry) * units if direction == "long" else (entry - exit_px) * units
        pnl -= cost
        if hasattr(closed_at, "to_pydatetime"):
            closed_at = closed_at.to_pydatetime()
        trades.append(SimTrade(closed_at=closed_at, direction=direction, realized_pnl=pnl, risk_amount=risk_amt))
        i = j
    return trades


def entry_mask_series(
    family: str,
    df: pd.DataFrame,
    *,
    direction: str,
    parameters: dict[str, Any],
    session_open: Callable[[datetime], bool] | None = None,
) -> pd.Series:
    close = df["close"]
    high = df["high"]
    low = df["low"]
    open_ = df["open"]

    if family == "donchian_breakout_v2":
        n = int(parameters.get("channel", 30))
        upper = high.rolling(n).max().shift(1)
        lower = low.rolling(n).min().shift(1)
        return (close > upper) if direction == "long" else (close < lower)

    if family == "atr_trailing_trend":
        slow = int(parameters.get("ema_slow", 55))
        es = ema(close, slow)
        slope = es.diff(5)
        return ((close > es) & (slope > 0)) if direction == "long" else ((close < es) & (slope < 0))

    if family == "ema_pullback_continue":
        fast = int(parameters.get("ema_fast", 12))
        slow = int(parameters.get("ema_slow", 34))
        ef, es = ema(close, fast), ema(close, slow)
        r = rsi(close, 14)
        if direction == "long":
            return (ef > es) & (close <= ef) & (close > es) & (r > 40)
        return (ef < es) & (close >= ef) & (close < es) & (r < 60)

    if family == "vol_expansion_v2":
        bb_u, _, bb_l = bollinger_bands(close, 20, float(parameters.get("bb_std", 2.2)))
        width = (bb_u - bb_l) / close.replace(0, np.nan)
        squeeze = width < width.rolling(80).quantile(0.15)
        return (squeeze.shift(1).fillna(False) & (close > bb_u.shift(1))) if direction == "long" else (
            squeeze.shift(1).fillna(False) & (close < bb_l.shift(1))
        )

    if family == "squeeze_release":
        bb_u, _, bb_l = bollinger_bands(close, 20, 2.0)
        kc = ema(close, 20)
        width = bb_u - bb_l
        tight = width < width.rolling(50).quantile(0.2)
        release = tight.shift(1).fillna(False) & ~tight
        return (release & (close > kc)) if direction == "long" else (release & (close < kc))

    if family == "vwap_session_revert":
        vol = df["volume"] if "volume" in df.columns else pd.Series(1.0, index=df.index)
        vwap = (close * vol).rolling(40).sum() / vol.rolling(40).sum().replace(0, np.nan)
        dev = (close - vwap) / close
        thr = float(parameters.get("dev", 0.004))
        if session_open is not None:
            sess = pd.Series([session_open(ts.to_pydatetime()) for ts in df.index], index=df.index)
        else:
            sess = pd.Series(True, index=df.index)
        return (sess & (dev < -thr)) if direction == "long" else (sess & (dev > thr))

    if family == "orb_continuation":
        day = pd.Series(df.index.date, index=df.index)
        bar_num = df.groupby(day).cumcount()
        or_bars = 4
        in_or = bar_num < or_bars
        or_high = high.where(in_or).groupby(day).transform("max")
        or_low = low.where(in_or).groupby(day).transform("min")
        after_or = bar_num >= or_bars
        return (after_or & (close > or_high)) if direction == "long" else (after_or & (close < or_low))

    if family == "prev_day_hl_break":
        day = pd.Series(df.index.date, index=df.index)
        pd_hi = high.groupby(day).max().shift(1).reindex(day).values
        pd_lo = low.groupby(day).min().shift(1).reindex(day).values
        pd_hi_s = pd.Series(pd_hi, index=df.index)
        pd_lo_s = pd.Series(pd_lo, index=df.index)
        return (close > pd_hi_s) if direction == "long" else (close < pd_lo_s)

    if family == "mtf_trend_ltf_entry":
        slow = ema(close, int(parameters.get("ema_slow", 50)))
        slope = slow.diff(3)
        r = rsi(close, 7)
        return ((close > slow) & (slope > 0) & (r < 55)) if direction == "long" else (
            (close < slow) & (slope < 0) & (r > 45)
        )

    if family == "rsi_divergence_mr":
        r = rsi(close, 14)
        roll_low = low.rolling(10).min()
        roll_high = high.rolling(10).max()
        if direction == "long":
            return (low <= roll_low) & (r > r.shift(5))
        return (high >= roll_high) & (r < r.shift(5))

    if family == "momentum_after_base":
        base = (high.rolling(12).max() - low.rolling(12).min()) / close
        quiet = base < base.rolling(60).quantile(0.25)
        mom = close.pct_change(3)
        return (quiet.shift(1).fillna(False) & (mom > 0.004)) if direction == "long" else (
            quiet.shift(1).fillna(False) & (mom < -0.004)
        )

    if family == "channel_mean_revert":
        n = int(parameters.get("channel", 25))
        mid = (high.rolling(n).max() + low.rolling(n).min()) / 2
        dev = (close - mid) / close
        thr = float(parameters.get("dev", 0.006))
        return (dev < -thr) if direction == "long" else (dev > thr)

    return pd.Series(False, index=df.index)


def backtest_family(
    family: str,
    *,
    candles: list,
    direction: str,
    parameters: dict[str, Any],
    session_open: Callable[[datetime], bool] | None = None,
) -> list[SimTrade]:
    if family not in SUPPORTED_FAMILIES:
        return []
    df = _df(candles)
    if len(df) < 150:
        return []
    mask = entry_mask_series(family, df, direction=direction, parameters=parameters, session_open=session_open)
    return _simulate_n1(
        df,
        direction=direction,
        entry_mask=mask.fillna(False),
        atr_sl=float(parameters.get("atr_sl", 1.5)),
        atr_tp=float(parameters.get("atr_tp", 2.5)),
        max_bars=int(parameters.get("max_bars", 20)),
    )
