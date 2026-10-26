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


def test_v32_new_entries_blocked_on_ibkr_and_kraken():
    for symbol, key in (
        ("AMD", "rsi_divergence_mr|v1|AMD|15m|long"),
        ("NVDA", "ema_pullback_continue|v2|NVDA|15m|long"),
        ("BTCUSD", "rsi_divergence_mr|v1|AMD|15m|long"),
    ):
        v = evaluate_live_sim_v2_policy(
            strategy_slug=V32_LIVE_SIM_STRATEGY_SLUG,
            symbol=symbol,
            timeframe="15m",
            parameter_overrides=_params_for_key(key),
        )
        assert not v.allowed
        assert v.reason == "V32_NEW_ENTRIES_BLOCKED"


def test_v32_unqualified_family_blocked(monkeypatch):
    monkeypatch.setattr("quantara_engine.live_sim.v2_policy.V32_NEW_ENTRIES_BLOCKED", False)
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


def test_v32_coin_new_entry_blocked():
    params = _params_for_key("vol_expansion_v2|v2|COIN|15m|short")
    v = evaluate_live_sim_v2_policy(
        strategy_slug=V32_LIVE_SIM_STRATEGY_SLUG,
        symbol="COIN",
        timeframe="15m",
        parameter_overrides=params,
    )
    assert not v.allowed
    assert v.reason == "V32_NEW_ENTRIES_BLOCKED"


def test_main_robots_not_paused():
    from quantara_engine.live_sim.phase3_constants import ROBOT_A_LIVE_SIM_SYMBOLS
    from quantara_engine.live_sim.v2_policy import MAIN_LIVE_SIM_STRATEGY_SLUGS

    assert not LIVE_SIM_PAUSED_STRATEGY_SLUGS
    probe_symbol = {
        "gold-trend-pullback": next(iter(ROBOT_A_LIVE_SIM_SYMBOLS)),
    }
    for slug in MAIN_LIVE_SIM_STRATEGY_SLUGS:
        sym = probe_symbol.get(slug, "ETHUSD")
        v = evaluate_live_sim_v2_policy(strategy_slug=slug, symbol=sym, timeframe="15m")
        assert v.allowed, (slug, sym, v.reason)


def test_v32_stale_fx_mark_blocked(monkeypatch):
    monkeypatch.setattr("quantara_engine.live_sim.v2_policy.V32_NEW_ENTRIES_BLOCKED", False)
    from datetime import datetime, timedelta, timezone

    params = _params_for_key("ema_pullback_continue|v1|AMD|15m|long")
    params["symbol"] = "EURUSD"
    stale = datetime.now(timezone.utc) - timedelta(hours=72)
    v = evaluate_live_sim_v2_policy(
        strategy_slug=V32_LIVE_SIM_STRATEGY_SLUG,
        symbol="EURUSD",
        timeframe="15m",
        parameter_overrides=params,
        signal_candle_timestamp=stale,
        execution_now=datetime.now(timezone.utc),
    )
    assert not v.allowed
    assert v.reason == "STALE_FX_MARK"


def test_v32_fail_classification_blocked(monkeypatch):
    monkeypatch.setattr("quantara_engine.live_sim.v2_policy.V32_NEW_ENTRIES_BLOCKED", False)
    params = _params_for_key("channel_mean_revert|v1|COIN|15m|short")
    v = evaluate_live_sim_v2_policy(
        strategy_slug=V32_LIVE_SIM_STRATEGY_SLUG,
        symbol="COIN",
        timeframe="15m",
        parameter_overrides=params,
    )
    assert not v.allowed
    assert v.reason == "V32_CANDIDATE_NOT_QUALIFIED"
