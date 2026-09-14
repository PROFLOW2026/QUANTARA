"""Focused tests: Live Sim close authority + multi-broker summary truth."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from quantara_engine.broker.execution_service import BrokerExecutionResult
from quantara_engine.live_sim.close_authority import (
    finalize_live_sim_position_close,
    live_sim_close_is_partial,
    live_sim_physical_close_succeeded,
)


def _res(**kwargs) -> BrokerExecutionResult:
    base = dict(
        accepted=True,
        shadow_only=False,
        physical_closed_qty=Decimal("0"),
        physical_opened_qty=Decimal("0"),
        fill_quantity=Decimal("0"),
        broker_order_id=None,
        broker_fill_id=None,
        realized_pnl=None,
        decision=None,
    )
    base.update(kwargs)
    return BrokerExecutionResult(**base)


def test_a_accepted_shadow_only_does_not_succeed():
    r = _res(accepted=True, shadow_only=True, physical_closed_qty=Decimal("0"))
    assert live_sim_physical_close_succeeded(r) is False


def test_a2_accepted_zero_physical_does_not_succeed():
    r = _res(accepted=True, shadow_only=False, physical_closed_qty=Decimal("0"), fill_quantity=Decimal("0"))
    assert live_sim_physical_close_succeeded(r) is False


def test_b_confirmed_physical_full_exit_succeeds():
    r = _res(
        accepted=True,
        shadow_only=False,
        physical_closed_qty=Decimal("0.039"),
        fill_quantity=Decimal("0.039"),
        broker_order_id="ord",
        broker_fill_id="fill",
    )
    assert live_sim_physical_close_succeeded(r) is True


def test_c_partial_close_detected():
    r = _res(
        accepted=True,
        shadow_only=False,
        physical_closed_qty=Decimal("0.01"),
        fill_quantity=Decimal("0.01"),
        broker_order_id="ord",
        broker_fill_id="fill",
    )
    assert live_sim_close_is_partial(r, requested_quantity=Decimal("0.039")) is True


def test_finalize_keeps_open_on_shadow_only():
    store = MagicMock()
    store.session = MagicMock()
    r = _res(accepted=True, shadow_only=True, physical_closed_qty=Decimal("0"))
    ok = finalize_live_sim_position_close(
        store,
        position_id="p1",
        closed_at=datetime.now(timezone.utc),
        account_slug="live-sim-kraken-like",
        instrument_symbol="BTCUSD",
        mark_price=Decimal("78000"),
        broker_res=r,
        requested_quantity=Decimal("0.039"),
    )
    assert ok is False
    store.session.execute.assert_not_called()


def test_finalize_closes_on_physical_success(monkeypatch):
    store = MagicMock()
    store.session = MagicMock()
    svc = MagicMock()
    monkeypatch.setattr(
        "quantara_engine.live_sim.close_authority.BrokerExecutionService",
        lambda *a, **k: svc,
    )
    monkeypatch.setattr(
        "quantara_engine.owner_portfolio.asset_ledger.refresh_all_asset_states_from_positions",
        lambda *a, **k: None,
    )
    r = _res(
        accepted=True,
        shadow_only=False,
        physical_closed_qty=Decimal("0.039"),
        fill_quantity=Decimal("0.039"),
        broker_order_id="ord",
        broker_fill_id="fill",
    )
    ok = finalize_live_sim_position_close(
        store,
        position_id="p1",
        closed_at=datetime.now(timezone.utc),
        account_slug="live-sim-kraken-like",
        instrument_symbol="BTCUSD",
        mark_price=Decimal("78000"),
        broker_res=r,
        requested_quantity=Decimal("0.039"),
    )
    assert ok is True
    assert store.session.execute.called
    svc.mark_to_market.assert_called_once()


def test_finalize_partial_updates_qty_not_closed(monkeypatch):
    store = MagicMock()
    store.session = MagicMock()
    svc = MagicMock()
    monkeypatch.setattr(
        "quantara_engine.live_sim.close_authority.BrokerExecutionService",
        lambda *a, **k: svc,
    )
    r = _res(
        accepted=True,
        shadow_only=False,
        physical_closed_qty=Decimal("0.01"),
        fill_quantity=Decimal("0.01"),
        broker_order_id="ord",
        broker_fill_id="fill",
    )
    ok = finalize_live_sim_position_close(
        store,
        position_id="p1",
        closed_at=datetime.now(timezone.utc),
        account_slug="live-sim-kraken-like",
        instrument_symbol="BTCUSD",
        mark_price=Decimal("78000"),
        broker_res=r,
        requested_quantity=Decimal("0.039"),
    )
    assert ok is False
    sql = str(store.session.execute.call_args[0][0])
    assert "quantity" in sql.lower()
    assert "closed" not in sql.lower() or "status = 'closed'" not in sql.lower()


def test_h_multi_broker_summary_excludes_legacy(monkeypatch):
    from quantara_engine.live_sim import analytics

    owner = SimpleNamespace(
        slug="live-sim-owner",
        target_capital=Decimal("10000"),
        total_equity=Decimal("10094.76"),
        total_cash=Decimal("10000"),
        total_balance=Decimal("10000"),
        total_realized_pnl=Decimal("0"),
        total_unrealized_pnl=Decimal("94.76"),
        total_gross_exposure=Decimal("5000"),
        total_net_exposure=Decimal("5000"),
        total_available_capital=Decimal("8000"),
        total_initial_margin_used=Decimal("500"),
        allocated_capital_sum=Decimal("10000"),
        allocation_remaining=Decimal("0"),
        global_execution_halted=False,
        broker_slices=[
            SimpleNamespace(
                slug="live-sim-ibkr-like",
                broker_vendor="ibkr",
                label_he="IBKR",
                allocated_capital=Decimal("7500"),
                cash=Decimal("7500"),
                equity=Decimal("7560"),
                available_margin=Decimal("7000"),
                realized_pnl=Decimal("0"),
                unrealized_pnl=Decimal("60"),
                gross_exposure=Decimal("3000"),
                connection_state="connected",
                enabled=True,
            ),
            SimpleNamespace(
                slug="live-sim-kraken-like",
                broker_vendor="kraken",
                label_he="Kraken",
                allocated_capital=Decimal("2500"),
                cash=Decimal("2500"),
                equity=Decimal("2534.76"),
                available_margin=Decimal("2000"),
                realized_pnl=Decimal("0"),
                unrealized_pnl=Decimal("34.76"),
                gross_exposure=Decimal("2000"),
                connection_state="connected",
                enabled=True,
            ),
            SimpleNamespace(
                slug="live-sim-10k",
                broker_vendor="paper",
                label_he="Legacy",
                allocated_capital=Decimal("10000"),
                cash=Decimal("10000"),
                equity=Decimal("10000"),
                available_margin=Decimal("10000"),
                realized_pnl=Decimal("0"),
                unrealized_pnl=Decimal("0"),
                gross_exposure=Decimal("0"),
                connection_state="paused",
                enabled=True,
            ),
        ],
        asset_slices=[],
    )

    store = MagicMock()
    session = MagicMock()
    store.session = session

    def _execute(sql, params=None):
        q = str(sql)
        m = MagicMock()
        if "FROM broker_accounts WHERE slug" in q and params and params.get("slug") == "live-sim-10k":
            m.mappings.return_value.first.return_value = {
                "id": "legacy",
                "starting_cash": 10000,
                "equity": 10000,
                "cash": 10000,
                "balance": 10000,
                "realized_pnl": 0,
                "unrealized_pnl": 0,
                "gross_exposure": 0,
                "net_exposure": 0,
                "available_margin": 10000,
                "activated_at": None,
                "risk_settings": {},
                "fees_paid": 0,
                "account_metadata": {},
                "initial_margin_used": 0,
            }
        elif "SELECT fees_paid, activated_at, risk_settings" in q:
            m.mappings.return_value.first.return_value = {
                "fees_paid": 0,
                "activated_at": None,
                "risk_settings": {},
            }
        elif "FROM live_sim_positions" in q and "status = 'open'" in q:
            m.mappings.return_value.all.return_value = []
        elif "FROM live_sim_positions" in q and "status = 'closed'" in q:
            m.mappings.return_value.all.return_value = []
        elif "live_sim_allocation_log" in q:
            m.mappings.return_value.first.return_value = {
                "accepted": 23,
                "rejected": 111,
                "total": 134,
            }
        elif "broker_fills f" in q and "COUNT" in q:
            m.scalar.return_value = 6
        elif "FROM broker_orders o" in q and "COUNT" in q:
            m.scalar.return_value = 9
        elif "FROM live_sim_positions p" in q and "COUNT" in q:
            m.scalar.return_value = 4
        elif "broker_orders" in q:
            m.scalar.return_value = 9
        elif "broker_fills" in q and "COUNT" in q:
            m.scalar.return_value = 6
        elif "live_sim_positions p" in q and "COUNT" in q:
            m.scalar.return_value = 4
        elif "broker_positions" in q:
            m.mappings.return_value.all.return_value = []
        else:
            m.mappings.return_value.first.return_value = {"cnt": 0, "wins": 0}
            m.mappings.return_value.all.return_value = []
            m.scalar.return_value = 0
        return m

    session.execute.side_effect = _execute

    monkeypatch.setattr(analytics, "is_multi_broker_live_sim_active", lambda *a, **k: True)
    monkeypatch.setattr(
        analytics,
        "list_active_live_sim_broker_account_ids",
        lambda *a, **k: ["ibkr-id", "kraken-id"],
    )
    monkeypatch.setattr(
        analytics,
        "list_active_live_sim_broker_account_slugs",
        lambda *a, **k: ["live-sim-ibkr-like", "live-sim-kraken-like"],
    )
    monkeypatch.setattr(analytics, "aggregate_owner_portfolio", lambda *a, **k: owner)
    monkeypatch.setattr(
        analytics,
        "OwnerPortfolioService",
        lambda store: SimpleNamespace(get_portfolio_row=lambda slug: {"multi_broker_mode_enabled": True}),
    )
    monkeypatch.setattr(analytics, "resolve_live_sim_audit_since", lambda *a, **k: None)
    monkeypatch.setattr(analytics, "list_recent_allocations", lambda *a, **k: [])
    monkeypatch.setattr(
        analytics,
        "load_risk_settings",
        lambda row: SimpleNamespace(
            high_water_mark=Decimal("10000"),
            daily_start_equity=Decimal("10000"),
            risk_per_trade_pct=Decimal("1"),
            max_total_open_sl_risk_pct=Decimal("6"),
            max_symbol_sl_risk_pct=Decimal("3"),
            max_group_sl_risk_pct=Decimal("4"),
            daily_loss_gate_pct=Decimal("3"),
            max_drawdown_gate_pct=Decimal("10"),
            concentration_mode="standard",
        ),
    )
    monkeypatch.setattr(
        analytics,
        "compute_open_sl_risk",
        lambda *a, **k: SimpleNamespace(total_sl_risk_usd=Decimal("100")),
    )
    monkeypatch.setattr(
        "quantara_engine.owner_portfolio.asset_ledger.refresh_all_asset_states_from_positions",
        lambda *a, **k: None,
    )

    summary = analytics.build_live_sim_summary(store)
    assert summary["available"] is True
    assert summary["legacy_excluded"] is True
    assert summary["equity"] == pytest.approx(10094.76)
    assert summary["authority"] == "active_owner_portfolio"
    assert all(b["slug"] != "live-sim-10k" for b in (summary.get("broker_breakdown") or []))
    assert summary["candidates"]["total"] == 134
    assert summary["candidates"]["accepted"] == 23
    assert summary["candidates"]["orders_sent"] == 9
    assert summary["candidates"]["fills"] == 6
    assert summary["candidates"]["positions_opened"] == 4


def test_g_btc_recovery_idempotent_already_flat(monkeypatch):
    from quantara_engine.live_sim import btc_desync_recovery as mod

    store = MagicMock()
    session = MagicMock()
    store.session = session

    def _execute(sql, params=None):
        q = str(sql)
        m = MagicMock()
        if "FROM live_sim_positions p" in q:
            m.mappings.return_value.first.return_value = {
                "id": mod.DEFAULT_BTC_POSITION_ID,
                "status": "closed",
                "instrument_id": "inst",
                "direction": "long",
                "quantity": Decimal("0.039"),
                "entry_price": Decimal("77000"),
                "stop_loss": Decimal("76000"),
                "take_profit": Decimal("78233.24"),
                "timeframe": "5m",
                "opportunity_key": "opp",
                "opened_at": datetime.now(timezone.utc),
                "closed_at": datetime.now(timezone.utc),
                "account_id": "acct",
                "symbol": "BTCUSD",
                "broker_account_slug": "live-sim-kraken-like",
            }
        elif "broker_positions" in q:
            m.mappings.return_value.first.return_value = {"qty": 0}
        elif "COUNT(*)" in q:
            m.scalar.return_value = 1
        else:
            m.mappings.return_value.first.return_value = None
            m.scalar.return_value = 0
        return m

    session.execute.side_effect = _execute
    out = mod.recover_live_sim_btc_shadow_broker_desync(store, dry_run=False)
    assert out["status"] == "already_recovered"
    assert out["duplicate_exits"] == 0


def test_l_asset_hwm_monotonic(monkeypatch):
    from quantara_engine.live_sim.asset_gate_settings import update_asset_high_water_mark

    store = MagicMock()
    session = MagicMock()
    store.session = session
    session.execute.return_value.scalar.return_value = Decimal("1270")
    hwm = update_asset_high_water_mark(store, "asset-1", Decimal("1260"))
    assert hwm == Decimal("1270")
    # No downward update write when equity fell
    assert session.execute.call_count == 1
