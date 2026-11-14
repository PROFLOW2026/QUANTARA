"""Experiment-reset integrity: stale risk baselines, slot attribution and funnel classification."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import MagicMock

from quantara_engine.learning.funnel import terminal_stage_for_no_signal
from quantara_engine.live_sim.dynamic_universe import (
    MIN_SLOT_DWELL_MINUTES,
    _slot_in_dwell,
)


def test_every_non_signal_reason_gets_a_terminal_stage():
    reasons = {
        "HOLD: downtrend valid, no rally yet": "hold_no_setup",
        "NO_SETUP: trend unclear": "strategy_filter_rejection",
        "breakout_already_consumed": "strategy_filter_rejection",
        "no_compression_detected": "strategy_filter_rejection",
        "strong_trend_no_mean_reversion": "strategy_filter_rejection",
        "insufficient_directional_strength": "strategy_filter_rejection",
        "waiting_for_breakout": "hold_no_setup",
        "opening_range_incomplete": "hold_no_setup",
        None: "hold_no_setup",
    }
    for reason, expected in reasons.items():
        assert terminal_stage_for_no_signal(reason) == expected, reason


def test_slot_dwell_blocks_rapid_rotation_but_not_stale_history():
    now = datetime(2026, 11, 14, 15, 0, tzinfo=timezone.utc)
    recent = {"slot_id": "s1", "rotated_at": (now - timedelta(minutes=30)).isoformat()}
    old = {
        "slot_id": "s2",
        "rotated_at": (now - timedelta(minutes=MIN_SLOT_DWELL_MINUTES + 1)).isoformat(),
    }
    history = [recent, old]
    assert _slot_in_dwell(history, "s1", now) is True
    assert _slot_in_dwell(history, "s2", now) is False
    assert _slot_in_dwell(history, "never-rotated", now) is False


def test_pre_experiment_asset_baseline_is_reanchored():
    from quantara_engine.live_sim.asset_gate_settings import (
        reanchor_pre_experiment_asset_baseline,
    )

    anchor = datetime(2026, 11, 15, 8, 0, tzinfo=timezone.utc)
    stale_updated = anchor - timedelta(days=3)

    calls: list[str] = []

    def _exec(stmt, params=None):
        sql = str(stmt)
        res = MagicMock()
        if "account_metadata" in sql:
            res.scalar.return_value = anchor.isoformat()
        elif "SELECT updated_at" in sql:
            res.mappings.return_value.first.return_value = {
                "updated_at": stale_updated,
                "current_cash": Decimal("1210"),
                "unrealized_pnl": Decimal("0"),
                "high_water_mark": Decimal("1400"),
                "daily_start_equity": Decimal("1390"),
                "daily_start_date": "2026-10-20",
            }
        elif "SELECT current_cash, unrealized_pnl" in sql:
            res.mappings.return_value.first.return_value = {
                "current_cash": Decimal("1210"),
                "unrealized_pnl": Decimal("0"),
            }
        else:
            calls.append(sql)
        return res

    store = MagicMock()
    store.session.execute.side_effect = _exec
    asset_row = {"id": "asset-1", "high_water_mark": Decimal("1400")}
    changed = reanchor_pre_experiment_asset_baseline(
        store, asset_row, broker_account_id="acct-1"
    )
    assert changed is True
    assert asset_row["high_water_mark"] == Decimal("1210")  # stale peak discarded
    assert asset_row["daily_start_equity"] == Decimal("1210")
    assert any("UPDATE owner_portfolio_asset_allocations" in c for c in calls)


def test_post_experiment_asset_baseline_is_kept():
    from quantara_engine.live_sim.asset_gate_settings import (
        reanchor_pre_experiment_asset_baseline,
    )

    anchor = datetime(2026, 11, 15, 8, 0, tzinfo=timezone.utc)

    def _exec(stmt, params=None):
        sql = str(stmt)
        res = MagicMock()
        if "account_metadata" in sql:
            res.scalar.return_value = anchor.isoformat()
        else:
            res.mappings.return_value.first.return_value = {
                "updated_at": anchor + timedelta(hours=2),
                "current_cash": Decimal("1250"),
                "unrealized_pnl": Decimal("0"),
            }
        return res

    store = MagicMock()
    store.session.execute.side_effect = _exec
    asset_row = {"id": "asset-1", "high_water_mark": Decimal("1300")}
    assert (
        reanchor_pre_experiment_asset_baseline(store, asset_row, broker_account_id="acct-1")
        is False
    )
    assert asset_row["high_water_mark"] == Decimal("1300")
