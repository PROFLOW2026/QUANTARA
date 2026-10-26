"""Experimental Live Sim risk profile defaults."""

from decimal import Decimal

from quantara_engine.execution.timing import (
    execution_grace_minutes,
    live_sim_freshness_max_age_minutes,
    stale_signal_max_age_minutes,
)
from quantara_engine.live_sim.constants import (
    DEFAULT_DAILY_LOSS_GATE_PCT,
    DEFAULT_MAX_DRAWDOWN_GATE_PCT,
    DEFAULT_MAX_TOTAL_OPEN_SL_RISK_PCT,
    DEFAULT_RISK_PER_TRADE_PCT,
)
from quantara_engine.live_sim.experimental_profile import (
    MAX_CONCURRENT_POSITIONS,
    RISK_PER_TRADE_PCT,
    risk_settings_patch,
)


def test_experimental_risk_defaults():
    assert DEFAULT_RISK_PER_TRADE_PCT == Decimal("2.50")
    assert DEFAULT_MAX_TOTAL_OPEN_SL_RISK_PCT == Decimal("15.00")
    assert DEFAULT_DAILY_LOSS_GATE_PCT == Decimal("10.00")
    assert DEFAULT_MAX_DRAWDOWN_GATE_PCT == Decimal("25.00")
    assert RISK_PER_TRADE_PCT == DEFAULT_RISK_PER_TRADE_PCT
    patch = risk_settings_patch()
    assert patch["risk_per_trade_pct"] == "2.50"
    assert MAX_CONCURRENT_POSITIONS == 8


def test_live_sim_freshness_extends_stale_gate():
    tf = "5m"
    assert live_sim_freshness_max_age_minutes(tf) == stale_signal_max_age_minutes(
        tf
    ) + execution_grace_minutes(tf)
