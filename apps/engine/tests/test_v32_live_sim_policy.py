"""V3.2 Live Sim routing — registry-backed qualification."""

from __future__ import annotations

from quantara_engine.live_sim.v2_policy import (
    LIVE_SIM_PAUSED_STRATEGY_SLUGS,
    V32_LIVE_SIM_STRATEGY_SLUG,
    evaluate_live_sim_v2_policy,
)
from quantara_engine.live_sim.v32_registry import load_v32_qualified_combinations


def _params_for_key(key: str) -> dict:
    for row in load_v32_qualified_combinations():
        if row["key"] == key:
            from quantara_engine.live_sim.v32_registry import parameter_overrides_for_combination

            return parameter_overrides_for_combination(row)
    raise KeyError(key)


def test_v32_amd_rsi_15m_long_allowed():
    params = _params_for_key("rsi_divergence_mr|v1|AMD|15m|long")
    v = evaluate_live_sim_v2_policy(
        strategy_slug=V32_LIVE_SIM_STRATEGY_SLUG,
        symbol="AMD",
        timeframe="15m",
        parameter_overrides=params,
    )
    assert v.allowed


def test_v32_nvda_ema_v2_15m_long_allowed():
    params = _params_for_key("ema_pullback_continue|v2|NVDA|15m|long")
    v = evaluate_live_sim_v2_policy(
        strategy_slug=V32_LIVE_SIM_STRATEGY_SLUG,
        symbol="NVDA",
        timeframe="15m",
        parameter_overrides=params,
    )
    assert v.allowed


def test_v32_unqualified_family_blocked():
    v = evaluate_live_sim_v2_policy(
        strategy_slug=V32_LIVE_SIM_STRATEGY_SLUG,
        symbol="AMD",
        timeframe="15m",
        parameter_overrides={
            "family": "not_a_real_family",
            "trade_direction": "long",
            "symbol": "AMD",
            "v32_variant_id": "v1",
        },
    )
    assert not v.allowed
    assert v.reason == "V32_CANDIDATE_NOT_QUALIFIED"


def test_v32_coin_qualified_short_allowed():
    params = _params_for_key("vol_expansion_v2|v2|COIN|15m|short")
    v = evaluate_live_sim_v2_policy(
        strategy_slug=V32_LIVE_SIM_STRATEGY_SLUG,
        symbol="COIN",
        timeframe="15m",
        parameter_overrides=params,
    )
    assert v.allowed


def test_robots_a_through_e_live_paused():
    for slug in LIVE_SIM_PAUSED_STRATEGY_SLUGS:
        v = evaluate_live_sim_v2_policy(strategy_slug=slug, symbol="AMD", timeframe="15m")
        assert not v.allowed
        assert v.reason == "ROBOT_LIVE_PAUSED"


def test_v32_fail_classification_blocked():
    params = _params_for_key("channel_mean_revert|v1|COIN|15m|short")
    v = evaluate_live_sim_v2_policy(
        strategy_slug=V32_LIVE_SIM_STRATEGY_SLUG,
        symbol="COIN",
        timeframe="15m",
        parameter_overrides=params,
    )
    assert not v.allowed
    assert v.reason == "V32_CANDIDATE_NOT_QUALIFIED"
