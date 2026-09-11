"""Fair round-robin live scheduling — Robot A and Robot B."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from quantara_engine.execution.strategy_scheduling import (
    ROBOT_A_LIVE_CURSOR_KEY,
    ROBOT_B_ORB_LIVE_CURSOR_KEY,
    load_scheduling_cursor,
    rotated_indices,
    save_scheduling_cursor,
)
from quantara_engine.market_data.active_universe import ACTIVE_DB_SYMBOLS
from quantara_workers.jobs.run_strategy import (
    _experiment_iteration_pairs,
    _process_experiment,
    _process_orb_live_sweep,
)


def test_rotated_indices_wraps():
    assert rotated_indices(4, 0) == [0, 1, 2, 3]
    assert rotated_indices(4, 2) == [2, 3, 0, 1]
    assert rotated_indices(8, 7) == [7, 0, 1, 2, 3, 4, 5, 6]


def test_cursor_persists_in_settings():
    store = MagicMock()
    store.get_settings_dict.return_value = {}
    save_scheduling_cursor(store, ROBOT_B_ORB_LIVE_CURSOR_KEY, 3)
    store.update_settings.assert_called_once()
    key, payload = store.update_settings.call_args[0]
    assert key == "strategy_scheduling_cursors"
    assert payload[ROBOT_B_ORB_LIVE_CURSOR_KEY] == 3

    store.get_settings_dict.return_value = {key: payload}
    assert load_scheduling_cursor(store, ROBOT_B_ORB_LIVE_CURSOR_KEY) == 3


def test_orb_live_sweep_rotates_start_symbol_across_cycles():
    symbols = list(ACTIVE_DB_SYMBOLS)
    store = MagicMock()
    store.get_settings_dict.return_value = {}
    entries = [
        {
            "instance": MagicMock(
                timeframe="5m",
                instrument_id=f"inst-{sym}",
            )
        }
        for sym in symbols
    ]

    seen_starts: list[str] = []

    def fake_group(*args, **kwargs):
        instrument = args[1]
        seen_starts.append(instrument.symbol)
        return 1, 1, {"backlog": 0}

    with patch(
        "quantara_workers.jobs.run_strategy._process_timeframe_group",
        side_effect=fake_group,
    ):
        with patch("quantara_workers.jobs.run_strategy._commit_progress"):
            for _ in range(3):
                store.get_instrument_by_symbol.side_effect = lambda sym: MagicMock(
                    id=f"inst-{sym}", symbol=sym
                )
                _process_orb_live_sweep(
                    store,
                    entries,
                    symbols,
                    "5m",
                    {},
                    datetime.now(timezone.utc),
                    time.perf_counter() + 999,
                )

    assert seen_starts[0] == symbols[0]
    assert seen_starts[1] == symbols[1]
    assert seen_starts[2] == symbols[2]


def test_robot_a_live_rotates_pair_cursor():
    symbols = ["BTCUSD", "ETHUSD", "COIN"]
    pairs = _experiment_iteration_pairs(symbols, ("5m", "15m"), order_by_timeframe_first=True)
    store = MagicMock()
    store.get_settings_dict.return_value = {}
    entries = [
        {
            "portfolio": MagicMock(),
            "instance": MagicMock(
                id="i1", timeframe=tf, instrument_id=f"inst-{sym}"
            ),
            "risk_profile": MagicMock(),
        }
        for tf in ("5m", "15m")
        for sym in symbols
    ]

    attempted: list[tuple[str, str]] = []

    def fake_tf_group(s, instrument, timeframe, group, *rest, **kwargs):
        attempted.append((timeframe, instrument.symbol))
        return 1, 1, {"backlog": 0}

    with patch("quantara_workers.jobs.run_strategy._process_timeframe_group", side_effect=fake_tf_group):
        with patch("quantara_workers.jobs.run_strategy._commit_progress"):
            store.get_instrument_by_symbol.side_effect = lambda sym: MagicMock(
                id=f"inst-{sym}", symbol=sym
            )
            for _ in range(2):
                _process_experiment(
                    store,
                    entries,
                    symbols,
                    ("5m", "15m"),
                    {},
                    datetime.now(timezone.utc),
                    live_only=True,
                    historical_only=False,
                    time_budget_sec=60,
                    deadline=time.perf_counter() + 999,
                    order_by_timeframe_first=True,
                    scheduling_cursor_key=ROBOT_A_LIVE_CURSOR_KEY,
                )

    assert attempted[0] == pairs[0]
    assert attempted[1] == pairs[1]


def test_orb_deadline_advances_cursor_after_full_rotation():
    symbols = ["BTCUSD", "ETHUSD", "NVDA"]
    store = MagicMock()
    store.get_settings_dict.return_value = {}
    entries = [
        {
            "instance": MagicMock(
                timeframe="5m",
                instrument_id=f"inst-{sym}",
            )
        }
        for sym in symbols
    ]

    with patch(
        "quantara_workers.jobs.run_strategy._process_timeframe_group",
        return_value=(1, 1, {"backlog": 0}),
    ):
        with patch("quantara_workers.jobs.run_strategy._commit_progress"):
            store.get_instrument_by_symbol.side_effect = lambda sym: MagicMock(
                id=f"inst-{sym}", symbol=sym
            )
            _process_orb_live_sweep(
                store,
                entries,
                symbols,
                "5m",
                {},
                datetime.now(timezone.utc),
                time.perf_counter() + 999,
            )

    saved = store.update_settings.call_args[0][1]
    assert saved[ROBOT_B_ORB_LIVE_CURSOR_KEY] == 0
