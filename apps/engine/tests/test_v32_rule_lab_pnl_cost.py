"""Stage-A rule_lab PnL must scale round-trip costs with notional."""

from __future__ import annotations

import pandas as pd

from quantara_engine.research.v3_2.rule_lab import _simulate_n1


def test_long_up_produces_positive_net_after_costs_high_price_asset():
    idx = pd.date_range("2026-01-01", periods=8, freq="15min", tz="UTC")
    # Bar 0 signals; enter bar 1 open 100_000; bar 2+ rally to TP
    df = pd.DataFrame(
        {
            "open": [99_000, 100_000, 100_500, 101_000, 101_500, 102_000, 102_500, 103_000],
            "high": [99_500, 100_200, 101_000, 101_800, 102_200, 102_800, 103_200, 103_500],
            "low": [98_800, 99_800, 100_200, 100_800, 101_500, 102_000, 102_400, 102_900],
            "close": [99_200, 100_100, 100_800, 101_500, 102_000, 102_600, 103_000, 103_200],
            "volume": [1.0] * 8,
        },
        index=idx,
    )
    mask = pd.Series([True] + [False] * 7, index=idx)
    trades = _simulate_n1(
        df,
        direction="long",
        entry_mask=mask,
        atr_sl=1.0,
        atr_tp=2.0,
        max_bars=6,
        cost_bps=12.0,
    )
    assert len(trades) == 1
    assert trades[0].realized_pnl > 0


def test_long_down_produces_negative_net():
    idx = pd.date_range("2026-01-01", periods=8, freq="15min", tz="UTC")
    df = pd.DataFrame(
        {
            "open": [50_000, 50_000, 49_500, 49_000, 48_500, 48_000, 47_500, 47_000],
            "high": [50_100, 50_050, 49_600, 49_100, 48_600, 48_100, 47_600, 47_100],
            "low": [49_900, 49_000, 48_500, 48_000, 47_500, 47_000, 46_500, 46_000],
            "close": [49_950, 49_200, 48_800, 48_200, 47_800, 47_200, 46_800, 46_500],
            "volume": [1.0] * 8,
        },
        index=idx,
    )
    mask = pd.Series([True] + [False] * 7, index=idx)
    trades = _simulate_n1(
        df,
        direction="long",
        entry_mask=mask,
        atr_sl=1.0,
        atr_tp=2.0,
        max_bars=6,
        cost_bps=12.0,
    )
    assert len(trades) == 1
    assert trades[0].realized_pnl < 0
