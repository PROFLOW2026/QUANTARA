"""Isolated proof: scan candidate → rotation readiness → slot swap → entry gate (fixtures)."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from quantara_engine.domain.types import Candle, Signal, SignalAction
from quantara_engine.live_sim.dynamic_universe import (
    evaluate_dynamic_universe_entry,
    symbol_strategy_ready_for_rotation,
    sync_dynamic_live_sim_universe,
)


def test_rotation_ready_off_session_with_history_not_live_fresh():
    """After RTH close, rotation may proceed when history exists; live entry stays strict."""
    store = MagicMock()
    inst = MagicMock(id="inst-meta")
    store.get_instrument_by_symbol.return_value = inst
    store.count_candles.return_value = 200
    last = datetime(2026, 10, 20, 20, 0, tzinfo=timezone.utc)
    store.latest_candle_timestamp.return_value = last
    now = datetime(2026, 10, 20, 22, 0, tzinfo=timezone.utc)

    with patch("quantara_engine.live_sim.dynamic_universe.get_asset") as ga:
        asset = MagicMock()
        asset.trading_sessions = "us_equity"
        ga.return_value = asset
        with patch(
            "quantara_engine.live_sim.dynamic_universe.session_allows_entries",
            return_value=False,
        ):
            assert symbol_strategy_ready_for_rotation(store, "META", now=now)

        with patch(
            "quantara_engine.live_sim.dynamic_universe.is_strategy_candle_eligible",
            return_value=(False, "stale_data"),
        ):
            assert not evaluate_dynamic_universe_entry(
                store, symbol="META", is_close=False
            ).allowed


def test_sync_rotates_idle_coin_slot_to_meta_when_scan_and_data_ready():
    portfolio_id = "port-1"
    slot_id = "slot-coin"
    session = MagicMock()
    store = MagicMock(session=session)
    store.get_instrument_by_symbol.return_value = MagicMock(id="i1")

    def _exec(stmt, params=None):
        m = MagicMock()
        sql = str(getattr(stmt, "text", stmt))
        if "owner_trading_portfolios WHERE slug" in sql:
            m.scalar.return_value = portfolio_id
        elif "live_sim_dynamic_universe_snapshots" in sql and "SELECT" in sql:
            m.mappings.return_value.first.return_value = None
        elif "owner_portfolio_asset_allocations" in sql and "SELECT" in sql:
            m.mappings.return_value.all.return_value = [
                {
                    "id": slot_id,
                    "canonical_symbol": "COIN",
                    "trade_count": 3,
                    "gross_exposure": 0,
                    "live_sim_new_entries_allowed": True,
                    "enabled": True,
                    "broker_account_id": "b1",
                },
                {
                    "id": "slot-nvda",
                    "canonical_symbol": "NVDA",
                    "trade_count": 0,
                    "gross_exposure": 0,
                    "live_sim_new_entries_allowed": True,
                    "enabled": True,
                    "broker_account_id": "b1",
                },
            ]
        elif "live_sim_positions" in sql:
            m.scalars.return_value.all.return_value = []
        elif "live_sim_allocation_log" in sql or "broker_orders" in sql or "order_intents" in sql:
            m.first.return_value = None
        elif "open_sl_risk" in sql:
            m.mappings.return_value.first.return_value = {"open_sl_risk_usd": 0, "gross_exposure": 0}
        else:
            m.scalar.return_value = True
            m.mappings.return_value.first.return_value = None
            m.first.return_value = None
        return m

    session.execute.side_effect = _exec

    scan_row = {"symbol": "META", "score": 12.0}

    with patch(
        "quantara_engine.live_sim.dynamic_universe.rank_active_universe",
        return_value=[scan_row],
    ):
        with patch(
            "quantara_engine.live_sim.dynamic_universe.ensure_rotation_candidate_data",
            return_value={"META": {"status": "already_ready"}},
        ):
            with patch(
                "quantara_engine.live_sim.dynamic_universe.symbol_strategy_ready_for_rotation",
                return_value=True,
            ):
                with patch(
                    "quantara_engine.live_sim.dynamic_universe.list_active_db_symbols",
                    return_value={"META", "COIN", "NVDA", "XAUUSD"},
                ):
                    with patch(
                        "quantara_engine.live_sim.dynamic_universe._symbol_has_global_broker_routing",
                        return_value=True,
                    ):
                        with patch(
                            "quantara_engine.live_sim.dynamic_universe._symbol_has_blocking_live_sim_activity",
                            return_value=False,
                        ):
                            out = sync_dynamic_live_sim_universe(store)

    assert out["ok"] is True
    assert out["rotations_applied"] == 1
    assert out["rotated_slots"][0]["from"] == "COIN"
    assert out["rotated_slots"][0]["to"] == "META"


def test_meta_signal_passes_dynamic_gate_when_active_and_fresh(monkeypatch):
    store = MagicMock()
    monkeypatch.setattr(
        "quantara_engine.live_sim.dynamic_universe.list_live_sim_active_trading_symbols",
        lambda _s: {"META", "XAUUSD"},
    )
    monkeypatch.setattr(
        "quantara_engine.live_sim.dynamic_universe.list_live_sim_manage_symbols",
        lambda _s: set(),
    )
    monkeypatch.setattr(
        "quantara_engine.live_sim.dynamic_universe.symbol_has_fresh_strategy_data",
        lambda *_a, **_k: True,
    )
    store.session.execute.return_value.scalar.return_value = True
    verdict = evaluate_dynamic_universe_entry(store, symbol="META")
    assert verdict.allowed

    signal = Signal(
        action=SignalAction.BUY,
        reason="fixture_connectivity_only",
        suggested_sl=Decimal("580"),
        suggested_tp=Decimal("620"),
    )
    candle = Candle(
        instrument_id="meta-id",
        timeframe="5m",
        timestamp=datetime(2026, 10, 21, 14, 35, tzinfo=timezone.utc),
        open=Decimal("600"),
        high=Decimal("601"),
        low=Decimal("599"),
        close=Decimal("600.5"),
    )
    assert signal.action == SignalAction.BUY
    assert candle.close > Decimal("0")
