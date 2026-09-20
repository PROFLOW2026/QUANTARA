"""Containment must block new entries but not lifecycle expiry/cleanup."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from quantara_engine.live_sim.allocator import resume_all_pending_live_sim_allocations
from quantara_engine.live_sim.candidate_log import allocation_lifecycle_state

TZ3 = ZoneInfo("Asia/Jerusalem")


def _containment_store() -> MagicMock:
    store = MagicMock()
    store.get_settings_dict.return_value = {
        "live_sim_integrity_containment": {"block_new_entries": True}
    }
    return store


def test_containment_on_pending_inside_window_stays_pending():
    store = _containment_store()
    signal_ts = datetime(2026, 9, 20, 5, 30, tzinfo=TZ3)
    exec_ts = datetime(2026, 9, 20, 5, 45, tzinfo=TZ3)
    inside_grace = datetime(2026, 9, 20, 6, 10, tzinfo=TZ3)

    with patch(
        "quantara_engine.live_sim.allocator.expire_stale_live_sim_allocations",
        return_value=0,
    ) as expire, patch(
        "quantara_engine.live_sim.candidate_log.reconcile_accepted_limbo_allocations",
        return_value=0,
    ) as reconcile, patch(
        "quantara_engine.live_sim.candidate_log.finalize_expired_accepted_limbo",
        return_value=0,
    ):
        report = resume_all_pending_live_sim_allocations(store, inside_grace)

    expire.assert_called_once_with(store, inside_grace)
    reconcile.assert_called_once_with(store, inside_grace)
    assert report["blocked"] is True
    assert report["expired"] == 0
    assert report["resumed"] == 0
    assert report["filled"] == 0


def test_containment_on_pending_past_window_runs_expiry_not_execution():
    store = _containment_store()
    past_grace = datetime(2026, 9, 20, 6, 30, tzinfo=TZ3)

    with patch(
        "quantara_engine.live_sim.allocator.expire_stale_live_sim_allocations",
        return_value=1,
    ) as expire, patch(
        "quantara_engine.live_sim.candidate_log.reconcile_accepted_limbo_allocations",
        return_value=0,
    ), patch(
        "quantara_engine.live_sim.candidate_log.finalize_expired_accepted_limbo",
        return_value=1,
    ), patch(
        "quantara_engine.live_sim.execution_routing.list_active_live_sim_broker_account_ids",
    ) as list_accounts, patch(
        "quantara_engine.live_sim.allocator._execute_accepted_allocation",
    ) as execute:
        report = resume_all_pending_live_sim_allocations(store, past_grace)

    expire.assert_called_once_with(store, past_grace)
    list_accounts.assert_not_called()
    execute.assert_not_called()
    assert report["blocked"] is True
    assert report["expired"] == 1
    assert report["finalized_expired"] == 1
    assert report["resumed"] == 0


def test_containment_on_expiry_reconciliation_runs_before_block():
    store = _containment_store()
    now = datetime(2026, 9, 20, 6, 30, tzinfo=timezone.utc)
    call_order: list[str] = []

    def _expire(*_args, **_kwargs):
        call_order.append("expire")
        return 0

    def _blocked(*_args, **_kwargs):
        call_order.append("blocked_check")
        return True

    with patch(
        "quantara_engine.live_sim.allocator.expire_stale_live_sim_allocations",
        side_effect=_expire,
    ), patch(
        "quantara_engine.live_sim.candidate_log.reconcile_accepted_limbo_allocations",
        side_effect=lambda *_a, **_k: call_order.append("reconcile") or 0,
    ), patch(
        "quantara_engine.live_sim.candidate_log.finalize_expired_accepted_limbo",
        side_effect=lambda *_a, **_k: call_order.append("finalize") or 0,
    ), patch(
        "quantara_engine.live_sim.integrity_containment.is_live_sim_entries_blocked",
        side_effect=_blocked,
    ):
        resume_all_pending_live_sim_allocations(store, now)

    assert call_order == ["expire", "reconcile", "finalize", "blocked_check"]


def test_expired_allocation_not_pending_execution():
    row = {
        "accepted": False,
        "broker_order_id": None,
        "live_sim_position_id": None,
        "metadata": {
            "pending_execution": False,
            "expired": True,
            "expiry_reason": "execution_window_passed",
        },
    }
    assert allocation_lifecycle_state(row) == "expired"


@pytest.mark.parametrize(
    "now",
    [
        datetime(2026, 9, 20, 6, 30, tzinfo=TZ3),
    ],
)
def test_expire_stale_marks_row_terminal(now: datetime):
    from quantara_engine.live_sim.candidate_log import expire_stale_live_sim_allocations

    signal_ts = datetime(2026, 9, 20, 5, 30, tzinfo=TZ3)
    exec_ts = datetime(2026, 9, 20, 5, 45, tzinfo=TZ3)
    created_at = datetime(2026, 9, 20, 5, 45, 19, tzinfo=TZ3)

    store = MagicMock()
    store.session.execute.return_value.mappings.return_value.all.return_value = [
        {
            "id": "48b2d4b1-f4b2-4b1d-9ea6-cd56213ad353",
            "signal_candle_timestamp": signal_ts,
            "timeframe": "15m",
            "created_at": created_at,
            "metadata": {
                "pending_execution": True,
                "execution_candle_timestamp": exec_ts.isoformat(),
            },
        }
    ]

    with patch("quantara_engine.live_sim.candidate_log.mark_allocation_expired") as mark_exp:
        expired = expire_stale_live_sim_allocations(store, now)

    assert expired == 1
    mark_exp.assert_called_once_with(
        store,
        "48b2d4b1-f4b2-4b1d-9ea6-cd56213ad353",
        reason="execution_window_passed",
    )
