"""Equal-asset drawdown scope — asset/broker/owner HWM must not cross scopes."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from quantara_engine.domain.types import Candle, Instrument, Signal, SignalAction
from quantara_engine.execution.timing import freshness_max_age_minutes
from quantara_engine.live_sim.asset_gate_settings import (
    load_asset_gate_settings,
    update_asset_high_water_mark,
)
from quantara_engine.live_sim.allocator import maybe_allocate_live_sim
from quantara_engine.live_sim.risk_policy import (
    LiveSimRiskSettings,
    OpenRiskSnapshot,
    evaluate_drawdown_gate,
    evaluate_entry_gates,
)
from quantara_engine.owner_portfolio.asset_allocation import (
    configure_equal_asset_allocations,
    deactivate_equal_asset_allocations,
    get_asset_allocation_row,
    is_equal_asset_mode_active,
)
from quantara_engine.owner_portfolio.global_risk import evaluate_owner_global_risk
from quantara_engine.owner_portfolio.service import LIVE_SIM_OWNER_SLUG
from sqlalchemy import text

from quantara_engine.broker.reconciliation_orchestrator import is_broker_execution_allowed


@pytest.fixture
def active_equal_asset(broker_test_store):
    store = broker_test_store
    if is_equal_asset_mode_active(store, LIVE_SIM_OWNER_SLUG):
        deactivate_equal_asset_allocations(store, LIVE_SIM_OWNER_SLUG)
    result = configure_equal_asset_allocations(store, LIVE_SIM_OWNER_SLUG, activate=True)
    assert result["ok"], result
    try:
        yield store
    finally:
        store.session.rollback()
        deactivate_equal_asset_allocations(store, LIVE_SIM_OWNER_SLUG)


def _broker_limits() -> LiveSimRiskSettings:
    return LiveSimRiskSettings(
        risk_per_trade_pct=Decimal("1"),
        max_total_open_sl_risk_pct=Decimal("3"),
        max_symbol_sl_risk_pct=Decimal("1"),
        max_group_sl_risk_pct=Decimal("2"),
        daily_loss_gate_pct=Decimal("2"),
        max_drawdown_gate_pct=Decimal("10"),
        concentration_mode="ENFORCE",
        high_water_mark=Decimal("2500"),
        daily_start_equity=Decimal("2500"),
        daily_start_date="2026-09-13",
    )


def test_asset_1250_vs_asset_hwm_1250_zero_drawdown():
    row = {
        "starting_allocated_capital": Decimal("1250"),
        "current_cash": Decimal("1250"),
        "unrealized_pnl": Decimal("0"),
        "high_water_mark": Decimal("1250"),
        "daily_start_equity": Decimal("1250"),
        "daily_start_date": "2026-09-13",
    }
    settings = load_asset_gate_settings(row, _broker_limits())
    gate = evaluate_entry_gates(
        settings=settings,
        equity=Decimal("1250"),
        realized_pnl_today=Decimal("0"),
        open_risk=OpenRiskSnapshot(Decimal("0"), {}, {}),
        proposed_risk_usd=Decimal("10"),
        symbol="BTCUSD",
    )
    assert gate.allowed


def test_kraken_2500_vs_kraken_hwm_2500_zero_drawdown():
    gate = evaluate_drawdown_gate(
        equity=Decimal("2500"),
        high_water_mark=Decimal("2500"),
        max_drawdown_gate_pct=Decimal("10"),
        scope_label="broker drawdown",
    )
    assert gate.allowed


def test_owner_10000_vs_owner_hwm_zero_drawdown(active_equal_asset):
    store = active_equal_asset
    verdict = evaluate_owner_global_risk(
        store,
        owner_slug=LIVE_SIM_OWNER_SLUG,
        symbol="BTCUSD",
        incremental_sl_risk_usd=Decimal("10"),
        broker_local_allowed=True,
    )
    assert verdict.allowed


def test_asset_1250_never_compared_to_kraken_hwm_2500():
    row = {
        "starting_allocated_capital": Decimal("1250"),
        "current_cash": Decimal("1250"),
        "unrealized_pnl": Decimal("0"),
        "high_water_mark": Decimal("1250"),
        "daily_start_equity": Decimal("1250"),
        "daily_start_date": "2026-09-13",
    }
    asset_settings = load_asset_gate_settings(row, _broker_limits())
    asset_gate = evaluate_entry_gates(
        settings=asset_settings,
        equity=Decimal("1250"),
        realized_pnl_today=Decimal("0"),
        open_risk=OpenRiskSnapshot(Decimal("0"), {}, {}),
        proposed_risk_usd=Decimal("10"),
        symbol="BTCUSD",
    )
    assert asset_gate.allowed

    broker_gate = evaluate_drawdown_gate(
        equity=Decimal("1250"),
        high_water_mark=Decimal("2500"),
        max_drawdown_gate_pct=Decimal("10"),
    )
    assert not broker_gate.allowed
    assert broker_gate.reason == "DRAWDOWN_GATE"


def test_asset_hwm_growth_and_drawdown_math(active_equal_asset):
    store = active_equal_asset
    row = get_asset_allocation_row(store, owner_slug=LIVE_SIM_OWNER_SLUG, canonical_symbol="BTCUSD")
    assert row is not None
    update_asset_high_water_mark(store, row["id"], Decimal("1400"))
    store.session.commit()
    row = get_asset_allocation_row(store, owner_slug=LIVE_SIM_OWNER_SLUG, canonical_symbol="BTCUSD")
    settings = load_asset_gate_settings(row, _broker_limits())
    settings = LiveSimRiskSettings(
        risk_per_trade_pct=settings.risk_per_trade_pct,
        max_total_open_sl_risk_pct=settings.max_total_open_sl_risk_pct,
        max_symbol_sl_risk_pct=settings.max_symbol_sl_risk_pct,
        max_group_sl_risk_pct=settings.max_group_sl_risk_pct,
        daily_loss_gate_pct=settings.daily_loss_gate_pct,
        max_drawdown_gate_pct=settings.max_drawdown_gate_pct,
        concentration_mode=settings.concentration_mode,
        high_water_mark=Decimal("1400"),
        daily_start_equity=settings.daily_start_equity,
        daily_start_date=settings.daily_start_date,
    )
    gate = evaluate_drawdown_gate(
        equity=Decimal("1260"),
        high_water_mark=settings.high_water_mark,
        max_drawdown_gate_pct=settings.max_drawdown_gate_pct,
    )
    dd_pct = (Decimal("1400") - Decimal("1260")) / Decimal("1400") * Decimal("100")
    assert dd_pct == Decimal("10")
    assert not gate.allowed


def test_broker_drawdown_independent_of_asset_drawdown():
    asset_gate = evaluate_drawdown_gate(
        equity=Decimal("1250"),
        high_water_mark=Decimal("1250"),
        max_drawdown_gate_pct=Decimal("10"),
    )
    broker_gate = evaluate_drawdown_gate(
        equity=Decimal("2200"),
        high_water_mark=Decimal("2500"),
        max_drawdown_gate_pct=Decimal("10"),
    )
    assert asset_gate.allowed
    assert not broker_gate.allowed


def test_owner_drawdown_independent_of_broker_drawdown(active_equal_asset):
    store = active_equal_asset
    owner = evaluate_owner_global_risk(
        store,
        owner_slug=LIVE_SIM_OWNER_SLUG,
        symbol="NVDA",
        incremental_sl_risk_usd=Decimal("10"),
        broker_local_allowed=True,
    )
    broker = evaluate_drawdown_gate(
        equity=Decimal("2000"),
        high_water_mark=Decimal("2500"),
        max_drawdown_gate_pct=Decimal("10"),
    )
    assert owner.allowed
    assert not broker.allowed


def _btc() -> Instrument:
    return Instrument(
        id="btc-id",
        symbol="BTCUSD",
        name="BTC/USD",
        asset_class="crypto",
        quote_currency="USD",
        quantity_step=Decimal("0.0001"),
        min_quantity=Decimal("0.0001"),
    )


def _instance(tf: str = "5m", slug: str = "gold-trend-pullback"):
    inst = MagicMock()
    inst.id = "inst-1"
    inst.timeframe = tf
    inst.strategy_slug = slug
    inst.strategy_version = "1.0.0"
    return inst


def _fresh_candidate_rejection_not_fake_drawdown(active_equal_asset, *, tf: str, slug: str, signal_ts: datetime):
    store = active_equal_asset
    candle = Candle(
        instrument_id="btc-id",
        timeframe=tf,
        timestamp=signal_ts,
        open=Decimal("77000"),
        high=Decimal("77100"),
        low=Decimal("76900"),
        close=Decimal("77000"),
        volume=Decimal("1"),
    )
    execution_now = signal_ts + timedelta(minutes=5)
    signal = Signal(
        action=SignalAction.BUY,
        reason="test",
        suggested_sl=Decimal("76500"),
        suggested_tp=Decimal("78000"),
    )
    with patch(
        "quantara_engine.live_sim.allocator.compute_open_sl_risk",
        return_value=OpenRiskSnapshot(Decimal("0"), {}, {}),
    ):
        with patch(
            "quantara_engine.live_sim.allocator.find_allocation_by_canonical",
            return_value=None,
        ):
            with patch(
                "quantara_engine.broker.reconciliation_orchestrator.is_broker_execution_allowed",
                return_value=True,
            ):
                with patch(
                    "quantara_engine.owner_portfolio.global_risk.evaluate_owner_global_risk",
                    return_value=MagicMock(allowed=True, reason=None, layer="owner_global"),
                ):
                    with patch(
                        "quantara_engine.owner_portfolio.asset_risk.evaluate_asset_envelope_risk",
                        return_value=MagicMock(allowed=True),
                    ):
                        result = maybe_allocate_live_sim(
                            store,
                            entry={"instance": _instance(tf, slug)},
                            instrument=_btc(),
                            candle=candle,
                            candles=[candle, candle],
                            candle_index=0,
                            signal=signal,
                            execution_now=execution_now,
                        )
    assert result.get("reason") != "DRAWDOWN_GATE"


def test_fresh_btc_candidate_not_fake_drawdown_gate(active_equal_asset):
    signal_ts = datetime(2026, 9, 13, 20, 0, tzinfo=timezone.utc)
    _fresh_candidate_rejection_not_fake_drawdown(
        active_equal_asset, tf="5m", slug="opening-range-breakout", signal_ts=signal_ts
    )


def test_stale_candidate_still_rejected(active_equal_asset):
    store = active_equal_asset
    signal_ts = datetime(2026, 9, 13, 15, 30, tzinfo=timezone.utc)
    execution_now = datetime(2026, 9, 13, 17, 5, tzinfo=timezone.utc)
    assert (execution_now - signal_ts).total_seconds() / 60 > freshness_max_age_minutes("15m")
    candle = Candle(
        instrument_id="btc-id",
        timeframe="15m",
        timestamp=signal_ts,
        open=Decimal("77000"),
        high=Decimal("77100"),
        low=Decimal("76900"),
        close=Decimal("77000"),
        volume=Decimal("1"),
    )
    signal = Signal(
        action=SignalAction.SELL,
        reason="test",
        suggested_sl=Decimal("78000"),
        suggested_tp=Decimal("76000"),
    )
    result = maybe_allocate_live_sim(
        store,
        entry={"instance": _instance("15m")},
        instrument=_btc(),
        candle=candle,
        candles=[candle],
        candle_index=0,
        signal=signal,
        execution_now=execution_now,
    )
    assert result["status"] == "rejected"
    assert result["reason"] == "STALE_SIGNAL"


def test_research_unchanged(active_equal_asset):
    store = active_equal_asset
    research = store.session.execute(
        text(
            """
            SELECT slug, is_active, execution_model::text
            FROM broker_accounts WHERE slug = 'quantara_paper_competition'
            """
        )
    ).mappings().first()
    assert research is not None
    assert research["slug"] == "quantara_paper_competition"
    assert is_broker_execution_allowed(store, "quantara_paper_competition")
