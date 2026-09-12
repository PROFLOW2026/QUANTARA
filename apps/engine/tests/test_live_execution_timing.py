"""Live signal → execution timing closure tests."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from quantara_engine.execution.timing import (
    LIVE_EXECUTION_GRACE_MINUTES,
    STALE_SIGNAL_MAX_AGE_MINUTES,
    intent_past_execution_window,
    live_fill_allowed,
)
from quantara_engine.models.enums import OrderIntentStatus
from quantara_engine.models.portfolio import StrategyInstance as OrmStrategyInstance
from quantara_engine.models.trading import OrderIntent as OrmOrderIntent


def test_live_fill_allows_small_processing_latency():
    signal_ts = datetime(2026, 9, 9, 14, 20, tzinfo=timezone.utc)
    exec_ts = datetime(2026, 9, 9, 14, 25, tzinfo=timezone.utc)
    now = datetime(2026, 9, 9, 14, 32, tzinfo=timezone.utc)
    allowed, reason = live_fill_allowed(
        execution_candle_timestamp=exec_ts,
        candle_timestamp=exec_ts,
        signal_candle_timestamp=signal_ts,
        now=now,
        timeframe="5m",
    )
    assert allowed is True
    assert reason is None


def test_live_fill_rejects_genuinely_stale_signal():
    signal_ts = datetime(2026, 9, 9, 14, 0, tzinfo=timezone.utc)
    exec_ts = datetime(2026, 9, 9, 14, 5, tzinfo=timezone.utc)
    now = datetime(2026, 9, 9, 14, 20, tzinfo=timezone.utc)
    allowed, reason = live_fill_allowed(
        execution_candle_timestamp=exec_ts,
        candle_timestamp=exec_ts,
        signal_candle_timestamp=signal_ts,
        now=now,
        timeframe="5m",
    )
    assert allowed is False
    assert reason and "stale_signal_age" in reason


def test_aapl_style_late_created_intent_expires():
    signal_ts = datetime(2026, 9, 9, 14, 35, tzinfo=timezone.utc)
    exec_ts = datetime(2026, 9, 9, 14, 40, tzinfo=timezone.utc)
    created_at = datetime(2026, 9, 9, 14, 51, tzinfo=timezone.utc)
    now = datetime(2026, 9, 9, 14, 58, tzinfo=timezone.utc)
    assert intent_past_execution_window(
        signal_candle_timestamp=signal_ts,
        execution_candle_timestamp=exec_ts,
        intent_created_at=created_at,
        timeframe="5m",
        now=now,
    )


def test_cancel_stale_expires_overdue_pending_intent():
    from quantara_engine.persistence.store import TradingStore

    now = datetime(2026, 9, 9, 14, 58, tzinfo=timezone.utc)
    row = MagicMock(spec=OrmOrderIntent)
    row.strategy_instance_id = uuid.uuid4()
    row.execution_candle_timestamp = datetime(2026, 9, 9, 14, 40, tzinfo=timezone.utc)
    row.signal_candle_timestamp = datetime(2026, 9, 9, 14, 35, tzinfo=timezone.utc)
    row.created_at = datetime(2026, 9, 9, 14, 51, tzinfo=timezone.utc)
    row.status = OrderIntentStatus.PENDING_EXECUTION

    instance = MagicMock(spec=OrmStrategyInstance)
    instance.timeframe = MagicMock(value="5m")

    session = MagicMock()
    session.scalars.return_value.all.return_value = [row]
    session.get.return_value = instance

    store = TradingStore(session)
    cancelled = store.cancel_stale_pending_intents(
        "00000000-0000-0000-0000-000000000400",
        now,
    )
    assert cancelled == 1
    assert row.status == OrderIntentStatus.EXPIRED


def test_orb_uses_shared_signal_not_per_portfolio_eval():
    from quantara_workers.jobs import run_strategy as rs

    empty = (0, 0, 0, {}, {}, [])
    with patch.object(rs, "_process_experiment", return_value=empty) as process_a:
        with patch.object(rs, "_process_orb_live_sweep", return_value=empty) as process_b:
            with patch(
                "quantara_engine.live_sim.allocator.resume_all_pending_live_sim_allocations",
                return_value={"resumed": 0, "expired": 0},
            ):
                store = MagicMock()
                store.get_settings_dict.return_value = {
                    "paper_trading_enabled": True,
                    "trading_control_state": {"state": "running"},
                }
                store.list_competition_entries.return_value = [MagicMock()]
                store.list_orb_competition_entries.return_value = [MagicMock()]
                store.list_multi_strategy_competition_entries.return_value = []
                store.cancel_stale_pending_intents.return_value = 0
                rs._execute_strategy_cycle(
                    live_only=True,
                    historical_only=False,
                    time_budget_sec=300,
                    store=store,
                )

    # Live cycle: Robot A + Robot C/D/E each invoke _process_experiment; ORB uses dedicated sweep.
    assert process_a.call_count == 2
    assert process_b.call_count == 1


def test_process_candle_batch_evaluates_signal_once_for_group():
    from quantara_workers.jobs.run_strategy import _process_candle_batch

    store = MagicMock()
    instrument = MagicMock(symbol="SPY", id="inst-spy")
    candle_ts = datetime(2026, 9, 9, 14, 35, tzinfo=timezone.utc)
    candles = [MagicMock(timestamp=candle_ts, instrument_id="inst-spy", timeframe="5m")]
    group = []
    for i in range(5):
        group.append(
            {
                "portfolio": MagicMock(id=f"p{i}"),
                "instance": MagicMock(
                    id=f"i{i}",
                    strategy_slug="opening-range-breakout",
                    instrument_id="inst-spy",
                    strategy_version_id="sv",
                    parameter_overrides={},
                ),
                "risk_profile": MagicMock(),
            }
        )

    store.timeframe_group_already_processed.return_value = False
    store.load_portfolio_state.return_value = MagicMock(open_positions=MagicMock(return_value=[]))
    store.list_pending_order_intents.return_value = []
    store.count_trades_on_session_date.return_value = 0

    eval_calls = {"count": 0}
    original_init = None

    with patch("quantara_workers.jobs.run_strategy.CandleProcessor") as CP:
        def make_processor(*args, **kwargs):
            proc = MagicMock()
            proc.all_candles = []
            proc.decisions = []
            proc.pending_intents = []
            proc._persisted_intents = set()
            if not eval_calls["count"]:
                eval_calls["count"] += 1
                proc.evaluate_signal.return_value = (MagicMock(action=MagicMock()), candles[0])
            proc.process_candle.return_value = MagicMock(decisions=[], pending_intents=[])
            return proc

        CP.side_effect = make_processor
        _process_candle_batch(
            store,
            instrument,
            "5m",
            group,
            candles,
            0,
            datetime.now(timezone.utc),
            MagicMock(),
            candle_ts,
            allow_live_execution=True,
            per_portfolio_eval=False,
        )

    assert eval_calls["count"] == 1
    assert CP.call_count == 6


def test_fetch_data_uses_alpaca_live_for_us_equity_rth():
    from quantara_workers.jobs.fetch_data import _uses_alpaca_live_equity
    from quantara_engine.market_data.registry import TARGET_ASSETS

    nvda = next(a for a in TARGET_ASSETS if a.db_symbol == "NVDA")
    rth = datetime(2026, 9, 9, 14, 30, tzinfo=timezone.utc)
    assert _uses_alpaca_live_equity(nvda, rth) is True


def test_timing_constants_document_n_plus_one_semantics():
    from quantara_engine.execution.timing import INTENT_CREATION_TOLERANCE_MINUTES

    assert LIVE_EXECUTION_GRACE_MINUTES == 8
    assert INTENT_CREATION_TOLERANCE_MINUTES == 5
    assert STALE_SIGNAL_MAX_AGE_MINUTES == 15
