"""Live-first strategy runner regression tests."""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from quantara_workers.jobs.run_strategy import (
    LIVE_CYCLE_MAX_SECONDS,
    LIVE_ROBOT_A_BUDGET_SEC,
    LIVE_ROBOT_B_BUDGET_SEC,
    _process_timeframe_group,
    run_strategy_historical_job,
    run_strategy_job,
)


def _mock_instrument(symbol="BTCUSD"):
    inst = MagicMock()
    inst.id = "inst-1"
    inst.symbol = symbol
    return inst


def _mock_group():
    entry = {
        "portfolio": MagicMock(id="p1"),
        "instance": MagicMock(id="si1"),
        "risk_profile": MagicMock(),
    }
    return [entry]


def test_live_only_processes_newest_index_not_historical():
    store = MagicMock()
    store.fully_processed_candle_timestamps.return_value = set()
    store.get_timeframe_execution_status.return_value = {"backlog": 5, "status": "catching_up"}
    candles = [MagicMock(timestamp=datetime(2026, 9, 9, h, m, tzinfo=timezone.utc)) for h, m in [
        (6, 0),
        (6, 5),
        (6, 10),
    ]]
    for c in candles:
        c.timestamp = c.timestamp.replace(tzinfo=timezone.utc)

    with patch("quantara_workers.jobs.run_strategy.STRATEGY_MIN_CANDLES", 3):
        with patch("quantara_workers.jobs.run_strategy._check_eligibility", return_value=(True, "eligible")):
            with patch("quantara_workers.jobs.run_strategy._ensure_candles", return_value=candles):
                with patch("quantara_workers.jobs.run_strategy._catchup_indices", return_value=[0, 1, 2]):
                    with patch(
                        "quantara_workers.jobs.run_strategy._latest_completed_timestamp",
                        return_value=candles[-1].timestamp,
                    ):
                        with patch("quantara_workers.jobs.run_strategy._process_candle_batch") as batch:
                            batch.return_value = 3
                            started = datetime(2026, 9, 9, 6, 15, tzinfo=timezone.utc)
                            processed, decisions, _ = _process_timeframe_group(
                                store,
                                _mock_instrument(),
                                "5m",
                                _mock_group(),
                                {},
                                started,
                                MagicMock(),
                                live_only=True,
                            )
    assert processed == 1
    assert decisions == 3
    assert batch.call_count == 1
    assert batch.call_args.kwargs["allow_live_execution"] is True
    assert batch.call_args.args[5] == 2


def test_historical_only_skips_live_index():
    store = MagicMock()
    store.fully_processed_candle_timestamps.return_value = set()
    store.get_timeframe_execution_status.return_value = {"backlog": 2, "status": "catching_up"}
    candles = [MagicMock() for _ in range(3)]
    for i, c in enumerate(candles):
        c.timestamp = datetime(2026, 9, 9, 6, i * 5, tzinfo=timezone.utc)

    with patch("quantara_workers.jobs.run_strategy.STRATEGY_MIN_CANDLES", 3):
        with patch("quantara_workers.jobs.run_strategy._check_eligibility", return_value=(True, "eligible")):
            with patch("quantara_workers.jobs.run_strategy._ensure_candles", return_value=candles):
                with patch("quantara_workers.jobs.run_strategy._catchup_indices", return_value=[0, 1, 2]):
                    with patch(
                        "quantara_workers.jobs.run_strategy._latest_completed_timestamp",
                        return_value=candles[-1].timestamp,
                    ):
                        with patch("quantara_workers.jobs.run_strategy._process_candle_batch") as batch:
                            batch.return_value = 1
                            started = datetime(2026, 9, 9, 6, 15, tzinfo=timezone.utc)
                            processed, _, _ = _process_timeframe_group(
                                store,
                                _mock_instrument(),
                                "5m",
                                _mock_group(),
                                {},
                                started,
                                MagicMock(),
                                live_only=True,
                                historical_only=True,
                            )
    assert processed == 2
    assert batch.call_count == 2
    for call in batch.call_args_list:
        assert call.kwargs["allow_live_execution"] is False
        assert call.args[5] in (0, 1)


def test_run_strategy_job_delegates_to_live_cycle():
    with patch("quantara_workers.jobs.run_strategy._execute_strategy_cycle") as execute:
        run_strategy_job()
        execute.assert_called_once_with(
            live_only=True,
            historical_only=False,
            time_budget_sec=LIVE_CYCLE_MAX_SECONDS,
            store=None,
        )


def test_run_strategy_historical_job_delegates_to_historical_cycle():
    with patch("quantara_workers.jobs.run_strategy._execute_strategy_cycle") as execute:
        run_strategy_historical_job()
        execute.assert_called_once()
        kwargs = execute.call_args.kwargs
        assert kwargs["historical_only"] is True
        assert kwargs["live_only"] is True


def test_live_budget_slices_sum_within_cycle_envelope():
    assert LIVE_ROBOT_A_BUDGET_SEC + LIVE_ROBOT_B_BUDGET_SEC <= LIVE_CYCLE_MAX_SECONDS
