"""Global pending-intent ordering must be deterministic and portfolio-neutral."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

from quantara_engine.domain.types import IntentStatus
from quantara_engine.execution.live_intents import _collect_pending_work


def _intent(iid: str, exec_ts: datetime, portfolio_id: str):
    return SimpleNamespace(
        id=iid,
        execution_candle_timestamp=exec_ts,
        status=IntentStatus.PENDING_EXECUTION,
    )


def test_collect_pending_work_sorts_globally_not_by_robot_order():
    ts = datetime(2026, 9, 11, 12, 0, tzinfo=timezone.utc)
    store = MagicMock()
    store.is_orb_competition_enabled.return_value = True
    store.list_competition_entries.return_value = [
        {
            "portfolio": SimpleNamespace(id="robot-a-1"),
            "instance": SimpleNamespace(id="inst-a", instrument_id="i1"),
        }
    ]
    store.list_orb_competition_entries.return_value = [
        {
            "portfolio": SimpleNamespace(id="robot-b-1"),
            "instance": SimpleNamespace(id="inst-b", instrument_id="i2"),
        }
    ]
    store.get_instrument_by_id.return_value = SimpleNamespace(id="i1")
    store.list_pending_order_intents.side_effect = lambda pid, _iid: {
        "robot-a-1": [_intent("aaa", ts, "robot-a-1")],
        "robot-b-1": [_intent("bbb", ts, "robot-b-1")],
    }[pid]

    work = _collect_pending_work(store, ts)
    keys = [w.sort_key for w in work]
    assert keys == sorted(keys)

    shuffled_entries = store.list_orb_competition_entries.return_value + store.list_competition_entries.return_value
    store.list_competition_entries.return_value = []
    store.list_orb_competition_entries.return_value = shuffled_entries
    work2 = _collect_pending_work(store, ts)
    assert [w.sort_key for w in work2] == keys
