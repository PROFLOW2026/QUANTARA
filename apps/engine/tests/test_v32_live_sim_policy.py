"""V3.2 P2 Live Sim routing — focused policy gates."""

from __future__ import annotations

from quantara_engine.live_sim.v2_policy import (
    LIVE_SIM_PAUSED_STRATEGY_SLUGS,
    V32_LIVE_SIM_STRATEGY_SLUG,
    evaluate_live_sim_v2_policy,
)
def test_v32_amd_rsi_15m_long_allowed():
    v = evaluate_live_sim_v2_policy(
        strategy_slug=V32_LIVE_SIM_STRATEGY_SLUG,
        symbol="AMD",
        timeframe="15m",
    )
    assert v.allowed


def test_v32_nvda_ema_15m_long_allowed():
    v = evaluate_live_sim_v2_policy(
        strategy_slug=V32_LIVE_SIM_STRATEGY_SLUG,
        symbol="NVDA",
        timeframe="15m",
    )
    assert v.allowed


def test_v32_amd_orb_5m_blocked():
    v = evaluate_live_sim_v2_policy(
        strategy_slug=V32_LIVE_SIM_STRATEGY_SLUG,
        symbol="AMD",
        timeframe="5m",
    )
    assert not v.allowed
    assert v.reason == "V32_COMBINATION_NOT_IN_PORTFOLIO"


def test_v32_nvda_other_timeframe_blocked():
    v = evaluate_live_sim_v2_policy(
        strategy_slug=V32_LIVE_SIM_STRATEGY_SLUG,
        symbol="NVDA",
        timeframe="5m",
    )
    assert not v.allowed


def test_v32_tsla_blocked_even_on_canonical_slug():
    v = evaluate_live_sim_v2_policy(
        strategy_slug=V32_LIVE_SIM_STRATEGY_SLUG,
        symbol="TSLA",
        timeframe="15m",
    )
    assert not v.allowed
    assert v.reason == "V32_COMBINATION_NOT_IN_PORTFOLIO"


def test_coin_live_blocked():
    v = evaluate_live_sim_v2_policy(
        strategy_slug=V32_LIVE_SIM_STRATEGY_SLUG,
        symbol="COIN",
        timeframe="15m",
    )
    assert not v.allowed
    assert v.reason == "V32_COMBINATION_NOT_IN_PORTFOLIO"


def test_tsla_live_blocked():
    v = evaluate_live_sim_v2_policy(
        strategy_slug=V32_LIVE_SIM_STRATEGY_SLUG,
        symbol="TSLA",
        timeframe="15m",
    )
    assert not v.allowed
    assert v.reason == "V32_COMBINATION_NOT_IN_PORTFOLIO"


def test_robots_a_through_e_live_paused():
    for slug in LIVE_SIM_PAUSED_STRATEGY_SLUGS:
        v = evaluate_live_sim_v2_policy(strategy_slug=slug, symbol="AMD", timeframe="15m")
        assert not v.allowed
        assert v.reason == "ROBOT_LIVE_PAUSED"


def test_legacy_ae_blocked_on_any_asset():
    v = evaluate_live_sim_v2_policy(
        strategy_slug="mean-reversion",
        symbol="BTCUSD",
        timeframe="5m",
    )
    assert not v.allowed
    assert v.reason == "ROBOT_LIVE_PAUSED"


def test_research_replay_isolation_default_off():
    from quantara_engine.persistence.store import TradingStore

    store = TradingStore.__new__(TradingStore)
    assert getattr(store, "research_replay_isolation", False) is False


def test_backtest_runner_sets_isolation_only_when_not_persisting():
    import inspect

    from quantara_engine.backtesting import runner as runner_mod

    src = inspect.getsource(runner_mod)
    assert "research_replay_isolation = not persist_backtest_record" in src
