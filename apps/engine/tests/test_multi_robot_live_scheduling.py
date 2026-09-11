"""Multi-robot live scheduling, freshness, snapshot, and decisions tests."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

from quantara_engine.domain.types import Mode, Portfolio, PortfolioStatus
from quantara_workers.jobs.run_strategy import (
    LIVE_CYCLE_MAX_SECONDS,
    LIVE_ROBOT_A_BUDGET_SEC,
    LIVE_ROBOT_B_BUDGET_SEC,
    STRATEGY_STALL_THRESHOLD_MINUTES,
    _experiment_iteration_pairs,
    _process_competition,
    _process_timeframe_group,
    strategy_freshness_summary,
)
from quantara_workers.jobs.snapshot import snapshot_job


def _mock_instrument(symbol="BTCUSD"):
    inst = MagicMock()
    inst.id = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"quantara.test.{symbol}"))
    inst.symbol = symbol
    return inst


def _portfolio(portfolio_id: str) -> Portfolio:
    return Portfolio(
        id=portfolio_id,
        name=f"Portfolio {portfolio_id}",
        mode=Mode.PAPER,
        initial_capital=Decimal("2000"),
        balance=Decimal("2000"),
        equity=Decimal("2000"),
        peak_equity=Decimal("2000"),
        status=PortfolioStatus.ACTIVE,
    )


def _entry(instance_id: str, portfolio_id: str, timeframe: str = "5m", instrument_id: str = "inst-1"):
    return {
        "portfolio": _portfolio(portfolio_id),
        "instance": MagicMock(id=instance_id, timeframe=timeframe, instrument_id=instrument_id),
        "risk_profile": MagicMock(),
    }


def test_live_iteration_order_timeframe_first():
    pairs = _experiment_iteration_pairs(
        ["BTCUSD", "EURUSD", "XAUUSD"],
        ("5m", "15m", "1h"),
        order_by_timeframe_first=True,
    )
    assert pairs[0] == ("5m", "BTCUSD")
    assert pairs[1] == ("5m", "EURUSD")
    assert pairs[2] == ("5m", "XAUUSD")
    assert pairs[3] == ("15m", "BTCUSD")


def test_large_robot_a_backlog_does_not_block_orb_live_pass():
    store = MagicMock()
    store.get_settings_dict.return_value = {"paper_trading_enabled": True}
    store.list_competition_entries.return_value = [_entry("a1", "p1")]
    store.list_orb_competition_entries.return_value = [_entry("b1", "p2", instrument_id="inst-SPY")]

    with patch("quantara_workers.jobs.run_strategy._process_experiment") as process_a:
        with patch("quantara_workers.jobs.run_strategy._process_orb_live_sweep") as process_b:
            process_a.return_value = (9, 15, 10, {"5m": {"backlog": 50}}, {"XAUUSD": {}}, [])
            process_b.return_value = (5, 25, 5, {"5m": {"backlog": 0}}, {"SPY": {}}, [])
            _process_competition(
                store,
                datetime.now(timezone.utc),
                run_id="run-1",
                live_only=True,
                historical_only=False,
                time_budget_sec=LIVE_CYCLE_MAX_SECONDS,
            )

    assert process_a.call_count == 1
    assert process_b.call_count == 1
    robot_a_kwargs = process_a.call_args.kwargs
    assert robot_a_kwargs["live_only"] is True
    assert robot_a_kwargs["historical_only"] is False
    assert robot_a_kwargs["order_by_timeframe_first"] is True


def test_live_pass_processes_only_newest_candle_not_backlog():
    store = MagicMock()
    store.get_timeframe_execution_status.return_value = {"backlog": 53, "status": "catching_up"}
    base = datetime(2026, 9, 9, 9, 25, tzinfo=timezone.utc)
    candles = [
        MagicMock(timestamp=base + timedelta(minutes=i * 5))
        for i in range(54)
    ]

    with patch("quantara_workers.jobs.run_strategy.STRATEGY_MIN_CANDLES", 3):
        with patch("quantara_workers.jobs.run_strategy._check_eligibility", return_value=(True, "eligible")):
            with patch("quantara_workers.jobs.run_strategy._ensure_candles", return_value=candles):
                with patch(
                    "quantara_workers.jobs.run_strategy._catchup_indices",
                    return_value=list(range(53)),
                ):
                    with patch(
                        "quantara_workers.jobs.run_strategy._latest_completed_timestamp",
                        return_value=candles[-1].timestamp,
                    ):
                        with patch("quantara_workers.jobs.run_strategy._process_candle_batch") as batch:
                            batch.return_value = 1
                            started = datetime(2026, 9, 9, 14, 0, tzinfo=timezone.utc)
                            processed, _, _ = _process_timeframe_group(
                                store,
                                _mock_instrument("XAUUSD"),
                                "5m",
                                [_entry("x1", "px1")],
                                {},
                                started,
                                MagicMock(),
                                live_only=True,
                                historical_only=False,
                            )
    assert processed == 1
    assert batch.call_count == 1
    assert batch.call_args.args[5] == 52
    assert batch.call_args.kwargs["allow_live_execution"] is True


def test_historical_backlog_cannot_create_live_entries():
    store = MagicMock()
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
                            _process_timeframe_group(
                                store,
                                _mock_instrument(),
                                "5m",
                                [_entry("a1", "p1")],
                                {},
                                started,
                                MagicMock(),
                                live_only=True,
                                historical_only=True,
                            )
    for c in batch.call_args_list:
        assert c.kwargs["allow_live_execution"] is False


def test_running_with_recent_start_is_healthy():
    now = datetime(2026, 9, 9, 14, 0, tzinfo=timezone.utc)
    store = MagicMock()
    store.get_settings_dict.return_value = {
        "worker_status:strategy_runner": {
            "status": "running",
            "cycle_started_at": (now - timedelta(minutes=2)).isoformat(),
            "last_evaluation_at": (now - timedelta(minutes=1)).isoformat(),
        },
        "worker_status:data_fetcher": {"status": "healthy"},
    }
    store.get_instrument_by_symbol.return_value = _mock_instrument("XAUUSD")
    store.latest_candle_timestamp.return_value = now - timedelta(minutes=5)

    summary = strategy_freshness_summary(store, now)
    assert summary["healthy"] is True
    assert summary["stalled"] is False


def test_running_beyond_stall_threshold_is_unhealthy():
    now = datetime(2026, 9, 9, 14, 0, tzinfo=timezone.utc)
    store = MagicMock()
    store.get_settings_dict.return_value = {
        "worker_status:strategy_runner": {
            "status": "running",
            "cycle_started_at": (
                now - timedelta(minutes=STRATEGY_STALL_THRESHOLD_MINUTES + 1)
            ).isoformat(),
        },
        "worker_status:data_fetcher": {"status": "healthy"},
    }
    store.get_instrument_by_symbol.return_value = _mock_instrument("XAUUSD")
    store.latest_candle_timestamp.return_value = now - timedelta(minutes=5)

    summary = strategy_freshness_summary(store, now)
    assert summary["healthy"] is False
    assert summary["stalled"] is True


def test_snapshot_includes_all_competition_portfolios():
    robot_a = [_entry(f"a{i}", f"p{i}") for i in range(15)]
    robot_b = [_entry(f"b{i}", f"pb{i}") for i in range(25)]
    store = MagicMock()
    store.list_all_competition_entries.return_value = (robot_a, robot_b, robot_a + robot_b)
    store.session = MagicMock()

    with patch(
        "quantara_workers.jobs.snapshot.batch_open_positions_by_portfolio",
        return_value={},
    ):
        with patch(
            "quantara_workers.jobs.snapshot.batch_latest_candle_closes",
            return_value={},
        ):
            snapshot_job(store=store)

    assert store.save_snapshots_batch.call_count == 1
    batch = store.save_snapshots_batch.call_args[0][0]
    assert len(batch) == 40
    status = store.update_worker_status.call_args[0][1]
    assert status["portfolios_snapshotted"] == 40


def test_decisions_by_asset_preserves_robot_identity():
    from quantara_engine.api.routes import decisions_by_asset

    store = MagicMock()
    store.resolve_instrument_display_symbols.return_value = {"inst-1": "BTC/USD", "inst-spy": "SPY"}
    store.build_instance_strategy_identity_map.return_value = {
        "ia1": {
            "robot_label": "Robot A",
            "strategy_slug": "gold-trend-pullback",
            "strategy_name": "Trend Pullback",
        },
        "ib1": {
            "robot_label": "Robot B",
            "strategy_slug": "opening-range-breakout",
            "strategy_name": "Opening Range Breakout",
        },
    }

    decision_a = MagicMock()
    decision_a.id = "d-a"
    decision_a.candle_timestamp = datetime(2026, 9, 9, 14, 0, tzinfo=timezone.utc)
    decision_a.decision_type.value = "hold"
    decision_a.message = "HOLD"
    decision_a.instrument_id = "inst-1"
    decision_a.strategy_instance_id = "ia1"
    decision_a.metadata = {}

    decision_b = MagicMock()
    decision_b.id = "d-b"
    decision_b.candle_timestamp = datetime(2026, 9, 9, 14, 0, tzinfo=timezone.utc)
    decision_b.decision_type.value = "hold"
    decision_b.message = "opening_range_building"
    decision_b.instrument_id = "inst-spy"
    decision_b.strategy_instance_id = "ib1"
    decision_b.metadata = {}

    store.list_latest_decisions_by_asset_timeframe.return_value = [decision_a, decision_b]
    store.list_competition_positions.return_value = []

    payload = decisions_by_asset(store, timeframe="5m")
    by_id = {row["id"]: row for row in payload["decisions"]}
    assert by_id["d-a"]["robot_label"] == "Robot A"
    assert by_id["d-a"]["strategy_slug"] == "gold-trend-pullback"
    assert by_id["d-b"]["robot_label"] == "Robot B"
    assert by_id["d-b"]["strategy_slug"] == "opening-range-breakout"


def test_live_budget_constants():
    assert LIVE_ROBOT_A_BUDGET_SEC + LIVE_ROBOT_B_BUDGET_SEC <= LIVE_CYCLE_MAX_SECONDS
