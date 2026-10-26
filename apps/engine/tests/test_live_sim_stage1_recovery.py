"""Stage 1 recovery: asset drawdown scope, UTC daily baseline, pause logging."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

from quantara_engine.domain.types import Candle, Instrument, Signal, SignalAction
from quantara_engine.live_sim.allocator import maybe_allocate_live_sim
from quantara_engine.live_sim.risk_policy import (
    LiveSimRiskSettings,
    OpenRiskSnapshot,
    evaluate_drawdown_gate,
    evaluate_entry_gates,
    resolve_utc_daily_start,
)


def _settings(*, hwm: str, daily_start: str, daily_date: str) -> LiveSimRiskSettings:
    return LiveSimRiskSettings(
        risk_per_trade_pct=Decimal("1"),
        max_total_open_sl_risk_pct=Decimal("100"),
        max_symbol_sl_risk_pct=Decimal("25"),
        max_group_sl_risk_pct=Decimal("50"),
        daily_loss_gate_pct=Decimal("2"),
        max_drawdown_gate_pct=Decimal("10"),
        concentration_mode="ENFORCE",
        high_water_mark=Decimal(hwm),
        daily_start_equity=Decimal(daily_start),
        daily_start_date=daily_date,
    )


def test_tsla_asset_drawdown_uses_asset_equity_not_owner_equity():
    """Owner equity above the asset mark must not hide a 26% asset loss."""
    gate = evaluate_entry_gates(
        settings=_settings(hwm="1270.68", daily_start="976.00", daily_date="2026-10-08"),
        equity=Decimal("9701.38"),
        mark_equity=Decimal("940.01"),
        mark_scope_label="asset drawdown",
        realized_pnl_today=Decimal("0"),
        open_risk=OpenRiskSnapshot(Decimal("0"), {}, {}),
        proposed_risk_usd=Decimal("10"),
        symbol="TSLA",
    )
    assert gate.allowed is False
    assert gate.reason == "DRAWDOWN_GATE"
    assert gate.detail is not None
    assert gate.detail.startswith("asset drawdown ")
    assert Decimal(gate.detail.split()[2].rstrip("%")) >= Decimal("26")


def test_healthy_asset_is_not_blocked_by_owner_equity_or_broker_ten_k_mark():
    gate = evaluate_entry_gates(
        settings=_settings(hwm="1327.25", daily_start="1275.22", daily_date="2026-10-08"),
        equity=Decimal("9701.38"),
        mark_equity=Decimal("1275.22"),
        mark_scope_label="asset drawdown",
        realized_pnl_today=Decimal("0"),
        open_risk=OpenRiskSnapshot(Decimal("0"), {}, {}),
        proposed_risk_usd=Decimal("10"),
        symbol="AMD",
    )
    assert gate.allowed is True
    assert gate.reason is None


def test_stop_loss_cap_stays_on_risk_budget_equity():
    """A 3% cap of the $10,000 budget allows $200. The same cap of the $940 asset does not."""
    settings = _settings(hwm="940.01", daily_start="940.01", daily_date="2026-10-08")
    settings = LiveSimRiskSettings(
        risk_per_trade_pct=settings.risk_per_trade_pct,
        max_total_open_sl_risk_pct=Decimal("3"),
        max_symbol_sl_risk_pct=settings.max_symbol_sl_risk_pct,
        max_group_sl_risk_pct=settings.max_group_sl_risk_pct,
        daily_loss_gate_pct=settings.daily_loss_gate_pct,
        max_drawdown_gate_pct=settings.max_drawdown_gate_pct,
        concentration_mode="OBSERVE",
        high_water_mark=settings.high_water_mark,
        daily_start_equity=settings.daily_start_equity,
        daily_start_date=settings.daily_start_date,
    )
    gate = evaluate_entry_gates(
        settings=settings,
        equity=Decimal("10000"),
        mark_equity=Decimal("940.01"),
        mark_scope_label="asset drawdown",
        realized_pnl_today=Decimal("0"),
        open_risk=OpenRiskSnapshot(Decimal("0"), {}, {}),
        proposed_risk_usd=Decimal("200"),
        symbol="XAUUSD",
    )
    assert gate.allowed is True


def test_owner_and_broker_drawdown_stay_on_their_own_marks():
    owner = evaluate_drawdown_gate(
        equity=Decimal("9701.38"),
        high_water_mark=Decimal("10000"),
        max_drawdown_gate_pct=Decimal("10"),
        scope_label="owner master drawdown",
    )
    assert owner.allowed is True

    broker = evaluate_drawdown_gate(
        equity=Decimal("7201.38"),
        high_water_mark=Decimal("7500"),
        max_drawdown_gate_pct=Decimal("10"),
        scope_label="broker drawdown",
    )
    assert broker.allowed is True
    assert broker.detail is None


def test_same_utc_day_keeps_baseline_and_intraday_is_not_a_midnight_open():
    same_day, day, status = resolve_utc_daily_start(
        stored_equity=Decimal("1299.30"),
        stored_date="2026-10-08",
        scope_equity=Decimal("1250"),
        now=datetime(2026, 10, 8, 21, 0, tzinfo=timezone.utc),
    )
    assert status == "kept"
    assert day == "2026-10-08"
    assert same_day == Decimal("1299.30")

    refused, refused_day, refused_status = resolve_utc_daily_start(
        stored_equity=Decimal("10000"),
        stored_date="2026-10-01",
        scope_equity=Decimal("7201.38"),
        now=datetime(2026, 10, 8, 0, 5, tzinfo=timezone.utc),
    )
    assert refused_status == "refused_unverified"
    assert refused_day == "2026-10-01"
    assert refused == Decimal("10000")

    foreign, foreign_day, foreign_status = resolve_utc_daily_start(
        stored_equity=Decimal("7500"),
        stored_date="2026-10-07",
        scope_equity=Decimal("7201.38"),
        verified_utc_open=Decimal("10000"),
        foreign_baselines=(Decimal("10000"),),
        now=datetime(2026, 10, 8, 0, 5, tzinfo=timezone.utc),
    )
    assert foreign_status == "refused_foreign"
    assert foreign == Decimal("7500")
    assert foreign_day == "2026-10-07"


def test_utc_date_follows_utc_not_local_offset():
    # 02:30 at UTC+3 is still the previous UTC date.
    local = datetime(2026, 10, 9, 2, 30, tzinfo=timezone(timedelta(hours=3)))
    _equity, day, status = resolve_utc_daily_start(
        stored_equity=Decimal("2500"),
        stored_date="2026-10-08",
        scope_equity=Decimal("2500"),
        now=local,
    )
    assert status == "kept"
    assert day == "2026-10-08"

    after_midnight = datetime(2026, 10, 9, 3, 30, tzinfo=timezone(timedelta(hours=3)))
    equity, day, status = resolve_utc_daily_start(
        stored_equity=Decimal("2500"),
        stored_date="2026-10-08",
        scope_equity=Decimal("2400"),
        verified_utc_open=Decimal("2500"),
        now=after_midnight,
    )
    assert status == "verified_open"
    assert day == "2026-10-09"
    assert equity == Decimal("2500")


def test_paused_robot_rejection_is_logged(monkeypatch):
    captured: dict = {}

    def _log(store, **kwargs):
        captured.update(kwargs)
        return "log-id"

    monkeypatch.setattr("quantara_engine.live_sim.allocator.log_allocation", _log)
    monkeypatch.setattr(
        "quantara_engine.live_sim.integrity_containment.is_live_sim_entries_blocked",
        lambda store: False,
    )
    runtime = SimpleNamespace(
        legacy_blocked=False,
        account={
            "is_active": True,
            "pending_owner_reset": False,
            "id": "acc-1",
            "starting_cash": Decimal("7500"),
        },
        account_slug="live-sim-ibkr-like",
        account_id="acc-1",
    )
    monkeypatch.setattr(
        "quantara_engine.live_sim.allocator.resolve_live_sim_runtime_context",
        lambda store, symbol, direction: runtime,
    )
    monkeypatch.setattr(
        "quantara_engine.live_sim.allocator._ensure_live_sim_account_ready",
        lambda store, account, slug: account,
    )
    now = datetime(2026, 10, 8, 14, 0, tzinfo=timezone.utc)
    candle = Candle(
        instrument_id="tsla-id",
        timeframe="15m",
        timestamp=now,
        open=Decimal("370"),
        high=Decimal("372"),
        low=Decimal("369"),
        close=Decimal("371"),
        volume=Decimal("1"),
    )
    instrument = Instrument(
        id="tsla-id",
        symbol="TSLA",
        name="Tesla",
        asset_class="stock",
        quote_currency="USD",
        quantity_step=Decimal("1"),
        min_quantity=Decimal("1"),
    )
    result = maybe_allocate_live_sim(
        MagicMock(),
        entry={
            "instance": SimpleNamespace(
                strategy_slug="v32-p2-live-sim",
                strategy_version="1.0.0",
                timeframe="15m",
                parameter_overrides={"v32_candidate_key": "rsi_divergence_mr|v1|AMD|15m|long"},
            )
        },
        instrument=instrument,
        candle=candle,
        candles=[candle],
        candle_index=0,
        signal=Signal(
            action=SignalAction.BUY,
            reason="test",
            suggested_sl=Decimal("360"),
            suggested_tp=Decimal("390"),
        ),
        execution_now=now,
    )
    assert result["status"] == "rejected"
    assert result["reason"] == "V32_NEW_ENTRIES_BLOCKED"
    assert captured["accepted"] is False
    assert captured["rejection_reason"] == "V32_NEW_ENTRIES_BLOCKED"
    assert captured["strategy_slug"] == "v32-p2-live-sim"
