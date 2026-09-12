"""Live simulation $10K account — isolation, dedup, risk gates."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from quantara_engine.broker.accounts import (
    LIVE_SIM_10K_ACCOUNT_SLUG,
    LIVE_SIM_VIRTUAL_PORTFOLIO_ID,
    RESEARCH_PAPER_ACCOUNT_SLUG,
)
from quantara_engine.broker.execution_service import BrokerExecutionService
from quantara_engine.live_sim.constants import REJECTION_HE
from quantara_engine.live_sim.opportunity import (
    live_sim_canonical_opportunity_key,
    live_sim_execution_idempotency_key,
)
from quantara_engine.live_sim.risk_policy import (
    GateResult,
    LiveSimRiskSettings,
    evaluate_entry_gates,
    load_risk_settings,
    target_risk_for_equity,
)


def test_account_slugs_distinct():
    assert RESEARCH_PAPER_ACCOUNT_SLUG != LIVE_SIM_10K_ACCOUNT_SLUG
    assert LIVE_SIM_10K_ACCOUNT_SLUG == "live-sim-10k"


def test_virtual_portfolio_not_research_range():
    assert LIVE_SIM_VIRTUAL_PORTFOLIO_ID.startswith("00000000")


def test_canonical_key_ignores_risk_tier():
    base = {
        "strategy_slug": "momentum-continuation",
        "strategy_version": "1.0.0",
        "symbol": "BTCUSD",
        "timeframe": "15m",
        "direction": "long",
        "opportunity_key": "pullback:BTCUSD:15m:long:2026-09-12T10:00:00",
    }
    k1 = live_sim_canonical_opportunity_key(**base)
    k2 = live_sim_canonical_opportunity_key(**base)
    assert k1 == k2
    other = live_sim_canonical_opportunity_key(**{**base, "direction": "short"})
    assert other != k1


def test_idempotency_per_account():
    canon = "abc123"
    r = live_sim_execution_idempotency_key(RESEARCH_PAPER_ACCOUNT_SLUG, canon)
    l = live_sim_execution_idempotency_key(LIVE_SIM_10K_ACCOUNT_SLUG, canon)
    assert r != l


def test_target_risk_from_equity():
    settings = LiveSimRiskSettings(
        risk_per_trade_pct=Decimal("1"),
        max_total_open_sl_risk_pct=Decimal("3"),
        max_symbol_sl_risk_pct=Decimal("1"),
        max_group_sl_risk_pct=Decimal("2"),
        daily_loss_gate_pct=Decimal("2"),
        max_drawdown_gate_pct=Decimal("10"),
        concentration_mode="ENFORCE",
        high_water_mark=Decimal("10000"),
        daily_start_equity=Decimal("10000"),
        daily_start_date="2026-09-12",
    )
    assert target_risk_for_equity(Decimal("10000"), settings) == Decimal("100.00")
    assert target_risk_for_equity(Decimal("10500"), settings) == Decimal("105.00")
    assert target_risk_for_equity(Decimal("9200"), settings) == Decimal("92.00")


def test_total_sl_risk_gate():
    settings = LiveSimRiskSettings(
        risk_per_trade_pct=Decimal("1"),
        max_total_open_sl_risk_pct=Decimal("3"),
        max_symbol_sl_risk_pct=Decimal("1"),
        max_group_sl_risk_pct=Decimal("2"),
        daily_loss_gate_pct=Decimal("2"),
        max_drawdown_gate_pct=Decimal("10"),
        concentration_mode="ENFORCE",
        high_water_mark=Decimal("10000"),
        daily_start_equity=Decimal("10000"),
        daily_start_date="2026-09-12",
    )
    from quantara_engine.live_sim.risk_policy import OpenRiskSnapshot

    open_risk = OpenRiskSnapshot(
        total_sl_risk_usd=Decimal("250"),
        by_symbol={"BTCUSD": Decimal("250")},
        by_group={"CRYPTO_RISK": Decimal("250")},
    )
    gate = evaluate_entry_gates(
        settings=settings,
        equity=Decimal("10000"),
        realized_pnl_today=Decimal("0"),
        open_risk=open_risk,
        proposed_risk_usd=Decimal("100"),
        symbol="BTCUSD",
    )
    assert not gate.allowed
    assert gate.reason == "TOTAL_SL_RISK_LIMIT"


def test_drawdown_gate_blocks_entry_only():
    settings = LiveSimRiskSettings(
        risk_per_trade_pct=Decimal("1"),
        max_total_open_sl_risk_pct=Decimal("3"),
        max_symbol_sl_risk_pct=Decimal("1"),
        max_group_sl_risk_pct=Decimal("2"),
        daily_loss_gate_pct=Decimal("2"),
        max_drawdown_gate_pct=Decimal("10"),
        concentration_mode="ENFORCE",
        high_water_mark=Decimal("10000"),
        daily_start_equity=Decimal("10000"),
        daily_start_date="2026-09-12",
    )
    from quantara_engine.live_sim.risk_policy import OpenRiskSnapshot

    open_risk = OpenRiskSnapshot(total_sl_risk_usd=Decimal("0"), by_symbol={}, by_group={})
    gate = evaluate_entry_gates(
        settings=settings,
        equity=Decimal("8900"),
        realized_pnl_today=Decimal("0"),
        open_risk=open_risk,
        proposed_risk_usd=Decimal("50"),
        symbol="ETHUSD",
    )
    assert not gate.allowed
    assert gate.reason == "DRAWDOWN_GATE"


def test_hebrew_rejection_labels():
    assert "מגבלת" in REJECTION_HE["TOTAL_SL_RISK_LIMIT"]
    assert "כפולה" in REJECTION_HE["DUPLICATE_OPPORTUNITY"]


def test_broker_service_account_isolation():
    store = MagicMock()
    svc_research = BrokerExecutionService(store, account_slug=RESEARCH_PAPER_ACCOUNT_SLUG)
    svc_live = BrokerExecutionService(store, account_slug=LIVE_SIM_10K_ACCOUNT_SLUG)
    assert svc_research.account_slug != svc_live.account_slug
    assert svc_live.profile.starting_cash == Decimal("10000")
    assert svc_research.profile.starting_cash == Decimal("320000")


def test_load_risk_settings_defaults():
    settings = load_risk_settings({"equity": 10000, "starting_cash": 10000, "risk_settings": {}})
    assert settings.risk_per_trade_pct == Decimal("1.00")
    assert settings.concentration_mode == "ENFORCE"
