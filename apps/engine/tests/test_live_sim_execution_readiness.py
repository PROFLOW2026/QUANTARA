"""Live Sim execution readiness — shared timing with Research + execute_intents hook."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from quantara_engine.domain.types import Candle, Direction, Instrument
from quantara_engine.execution.timing import (
    is_execution_candle_ready,
    next_execution_timestamp,
    normalize_utc,
    resolve_execution_candle,
)
from quantara_engine.live_sim.allocator import resume_all_pending_live_sim_allocations


TZ3 = timezone(timedelta(hours=3))


def _eth() -> Instrument:
    return Instrument(
        id="eth-id",
        symbol="ETHUSD",
        name="ETH/USD",
        asset_class="crypto",
        quote_currency="USD",
        quantity_step=Decimal("0.0001"),
        min_quantity=Decimal("0.0001"),
    )


def _candle(ts: datetime, close: str = "2525") -> Candle:
    return Candle(
        instrument_id="eth-id",
        timeframe="15m",
        timestamp=ts,
        open=Decimal(close),
        high=Decimal(close),
        low=Decimal(close),
        close=Decimal(close),
        volume=Decimal("1"),
    )


@pytest.mark.parametrize("timeframe", ["5m", "15m", "1h"])
def test_research_and_live_sim_readiness_share_semantics(timeframe):
    signal_ts = datetime(2026, 9, 13, 0, 0, tzinfo=TZ3)
    exec_ts = next_execution_timestamp(signal_ts, timeframe)
    before_close = exec_ts + timedelta(minutes={"5m": 4, "15m": 14, "1h": 59}[timeframe])
    after_close = exec_ts + timedelta(minutes={"5m": 5, "15m": 15, "1h": 60}[timeframe])

    allowed_before, reason_before = is_execution_candle_ready(
        signal_candle_timestamp=signal_ts,
        execution_candle_timestamp=exec_ts,
        timeframe=timeframe,
        now=before_close,
    )
    allowed_after, _ = is_execution_candle_ready(
        signal_candle_timestamp=signal_ts,
        execution_candle_timestamp=exec_ts,
        timeframe=timeframe,
        now=after_close,
    )
    assert allowed_before is False
    assert reason_before == "execution_bar_not_complete"
    assert allowed_after is True


def test_resolve_execution_candle_matches_normalized_timestamps():
    signal_ts = datetime(2026, 9, 13, 0, 0, tzinfo=TZ3)
    exec_ts = datetime(2026, 9, 13, 0, 15, tzinfo=TZ3)
    candles = [
        _candle(signal_ts),
        _candle(exec_ts),
    ]
    candle, resolved = resolve_execution_candle(candles, signal_ts, "15m")
    assert candle is not None
    assert normalize_utc(candle.timestamp) == normalize_utc(exec_ts)
    assert normalize_utc(resolved) == normalize_utc(exec_ts)


def test_eth_15m_pending_before_close_executable_after():
    signal_ts = datetime(2026, 9, 13, 0, 0, tzinfo=TZ3)
    exec_ts = datetime(2026, 9, 13, 0, 15, tzinfo=TZ3)
    before = datetime(2026, 9, 13, 0, 29, tzinfo=TZ3)
    after = datetime(2026, 9, 13, 0, 30, 32, tzinfo=TZ3)

    assert is_execution_candle_ready(
        signal_candle_timestamp=signal_ts,
        execution_candle_timestamp=exec_ts,
        timeframe="15m",
        now=before,
    ) == (False, "execution_bar_not_complete")
    assert is_execution_candle_ready(
        signal_candle_timestamp=signal_ts,
        execution_candle_timestamp=exec_ts,
        timeframe="15m",
        now=after,
    ) == (True, None)


def test_execute_intents_invokes_live_sim_resume():
    from quantara_engine.execution.live_intents import execute_pending_intents_live

    store = MagicMock()
    store.get_settings_dict.return_value = {"trading_control": {}}
    store.list_all_competition_entries.return_value = ([], [], [])
    store.session.commit = MagicMock()

    with patch(
        "quantara_engine.trading.trading_controls.allows_new_entries",
        return_value=True,
    ), patch(
        "quantara_engine.execution.live_intents._expire_stale_intents",
        return_value=0,
    ), patch(
        "quantara_engine.execution.live_intents._collect_pending_work",
        return_value=[],
    ), patch(
        "quantara_engine.live_sim.allocator.resume_all_pending_live_sim_allocations",
        return_value={"resumed": 1, "expired": 0},
    ) as resume:
        report = execute_pending_intents_live(store, datetime.now(timezone.utc))
        resume.assert_called_once()
        assert report["live_sim_resumed"] == 1


def test_resume_skips_already_executed_allocations():
    store = MagicMock()
    store.session.execute.return_value.mappings.return_value.all.return_value = []
    with patch(
        "quantara_engine.live_sim.allocator._account_row",
        return_value={"id": "acct", "is_active": True, "equity": 10000, "starting_cash": 10000},
    ), patch(
        "quantara_engine.live_sim.allocator.expire_stale_live_sim_allocations",
        return_value=0,
    ):
        report = resume_all_pending_live_sim_allocations(
            store, datetime(2026, 9, 13, 0, 45, tzinfo=TZ3)
        )
    assert report["resumed"] == 0
