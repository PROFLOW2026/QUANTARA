"""Spot crypto V3.2 rows — short directions must not enter Live Sim."""

from __future__ import annotations

from quantara_engine.live_sim.v32_registry import v32_row_live_sim_eligible


def test_crypto_short_pass_row_not_live_eligible():
    row = {
        "key": "mtf_trend_ltf_entry|v1|BTCUSD|15m|short",
        "asset": "BTCUSD",
        "direction": "short",
        "classification": "PASS",
    }
    assert not v32_row_live_sim_eligible(row)


def test_crypto_long_pass_row_live_eligible():
    row = {
        "key": "mtf_trend_ltf_entry|v1|BTCUSD|15m|long",
        "asset": "BTCUSD",
        "direction": "long",
        "classification": "PASS",
    }
    assert v32_row_live_sim_eligible(row)
