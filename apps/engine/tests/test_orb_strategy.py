"""Opening Range Breakout v1.0.0 tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import pytest

from quantara_engine.domain.types import Candle, SignalAction, StrategyContext
from quantara_engine.market_data.sessions import US_EASTERN
from quantara_engine.strategies.gold_trend_pullback.v1_0_0 import GoldTrendPullbackV1
from quantara_engine.strategies.opening_range_breakout.session import (
    compute_opening_range,
    expected_opening_range_candle_opens,
    is_entry_cutoff_passed,
    is_opening_range_complete,
)
from quantara_engine.strategies.opening_range_breakout.v1_0_0 import OpeningRangeBreakoutV1
from quantara_engine.strategies.opening_range_breakout.indicators import atr, candles_to_df

ET = US_EASTERN


def _session_date(year: int, month: int, day: int):
    return datetime(year, month, day, tzinfo=ET).date()


def _orb_candle(local_dt: datetime, o: str, h: str, l: str, c: str) -> Candle:
    ts = local_dt.astimezone(timezone.utc)
    return Candle(
        instrument_id="spy-id",
        timeframe="5m",
        timestamp=ts,
        open=Decimal(o),
        high=Decimal(h),
        low=Decimal(l),
        close=Decimal(c),
        volume=Decimal("1000"),
    )


def _opening_range_candles(session_date, *, high: str = "501", low: str = "499") -> list[Candle]:
    mids = ["499.5", "500", "499.8", "500.2", "499.6", "500.1"]
    candles = []
    for i, ts in enumerate(expected_opening_range_candle_opens(session_date)):
        base = Decimal(mids[i])
        candles.append(
            Candle(
                instrument_id="spy-id",
                timeframe="5m",
                timestamp=ts,
                open=base,
                high=Decimal(high) if i == 2 else base + Decimal("0.5"),
                low=Decimal(low) if i == 4 else base - Decimal("0.5"),
                close=base,
                volume=Decimal("1000"),
            )
        )
    return candles


def _atr_warmup(session_date, count: int = 14) -> list[Candle]:
    start = datetime.combine(session_date, datetime.min.time(), tzinfo=ET) - timedelta(days=5)
    candles = []
    price = Decimal("490")
    for i in range(count):
        ts = (start + timedelta(minutes=5 * i)).astimezone(timezone.utc)
        candles.append(
            Candle(
                instrument_id="spy-id",
                timeframe="5m",
                timestamp=ts,
                open=price,
                high=price + Decimal("1"),
                low=price - Decimal("1"),
                close=price,
                volume=Decimal("100"),
            )
        )
    return candles


def test_opening_range_calculates_correctly():
    session = _session_date(2026, 3, 10)
    candles = _opening_range_candles(session, high="502", low="498")
    opening = compute_opening_range(candles, session)
    assert opening is not None
    assert opening.candle_count == 6
    assert float(opening.high) == 502.0
    assert float(opening.low) == 498.0
    assert float(opening.size) == 4.0


def test_no_signal_before_range_complete():
    strategy = OpeningRangeBreakoutV1()
    session = _session_date(2026, 3, 10)
    candles = _atr_warmup(session) + _opening_range_candles(session)[:3]
    ctx = StrategyContext("spy-id", "5m", {}, runtime={"trades_today": 0})
    at_945 = datetime(2026, 3, 10, 9, 45, tzinfo=ET).astimezone(timezone.utc)
    signal = strategy.evaluate(candles + [_orb_candle(datetime(2026, 3, 10, 9, 45, tzinfo=ET), "500", "500.5", "499.5", "500")], ctx)
    assert signal.action == SignalAction.HOLD
    assert "opening_range" in signal.reason


def test_close_above_range_high_buy_signal():
    strategy = OpeningRangeBreakoutV1()
    session = _session_date(2026, 3, 10)
    candles = _atr_warmup(session) + _opening_range_candles(session, high="500", low="498")
    breakout = _orb_candle(datetime(2026, 3, 10, 10, 0, tzinfo=ET), "500", "501.5", "499.8", "501.2")
    ctx = StrategyContext("spy-id", "5m", {}, runtime={"trades_today": 0})
    signal = strategy.evaluate(candles + [breakout], ctx)
    assert signal.action == SignalAction.BUY
    assert signal.reason == "breakout_long_confirmed"
    assert signal.suggested_sl is not None
    assert signal.suggested_tp is not None


def test_wick_above_close_below_no_buy():
    strategy = OpeningRangeBreakoutV1()
    session = _session_date(2026, 3, 10)
    candles = _atr_warmup(session) + _opening_range_candles(session, high="500", low="498")
    wick_only = _orb_candle(datetime(2026, 3, 10, 10, 0, tzinfo=ET), "499.5", "501.5", "499.0", "499.8")
    ctx = StrategyContext("spy-id", "5m", {}, runtime={"trades_today": 0})
    signal = strategy.evaluate(candles + [wick_only], ctx)
    assert signal.action != SignalAction.BUY


def test_close_below_range_low_sell_signal():
    strategy = OpeningRangeBreakoutV1()
    session = _session_date(2026, 3, 10)
    candles = _atr_warmup(session) + _opening_range_candles(session, high="500", low="498")
    breakdown = _orb_candle(datetime(2026, 3, 10, 10, 0, tzinfo=ET), "498.5", "499", "497.2", "497.5")
    ctx = StrategyContext("spy-id", "5m", {}, runtime={"trades_today": 0})
    signal = strategy.evaluate(candles + [breakdown], ctx)
    assert signal.action == SignalAction.SELL
    assert signal.reason == "breakout_short_confirmed"


def test_second_trade_same_day_blocked():
    strategy = OpeningRangeBreakoutV1()
    session = _session_date(2026, 3, 10)
    candles = _atr_warmup(session) + _opening_range_candles(session, high="500", low="498")
    breakout = _orb_candle(datetime(2026, 3, 10, 10, 0, tzinfo=ET), "500", "501.5", "499.8", "501.2")
    ctx = StrategyContext("spy-id", "5m", {}, runtime={"trades_today": 1})
    signal = strategy.evaluate(candles + [breakout], ctx)
    assert signal.action == SignalAction.HOLD
    assert signal.reason == "trade_already_taken_today"


def test_no_entry_after_1530_et():
    strategy = OpeningRangeBreakoutV1()
    session = _session_date(2026, 3, 10)
    candles = _atr_warmup(session) + _opening_range_candles(session, high="500", low="498")
    late = _orb_candle(datetime(2026, 3, 10, 15, 35, tzinfo=ET), "500", "502", "499", "501.5")
    ctx = StrategyContext("spy-id", "5m", {}, runtime={"trades_today": 0})
    signal = strategy.evaluate(candles + [late], ctx)
    assert signal.action == SignalAction.HOLD
    assert signal.reason == "entry_cutoff_passed"
    assert is_entry_cutoff_passed(late.timestamp)


def test_session_close_emits_close_with_open_position():
    strategy = OpeningRangeBreakoutV1()
    session = _session_date(2026, 3, 10)
    candles = _atr_warmup(session) + _opening_range_candles(session)
    late = _orb_candle(datetime(2026, 3, 10, 15, 55, tzinfo=ET), "500", "500.5", "499.5", "500")
    ctx = StrategyContext("spy-id", "5m", {}, runtime={"trades_today": 1, "has_open_position": True})
    signal = strategy.evaluate(candles + [late], ctx)
    assert signal.action == SignalAction.CLOSE
    assert "session_close" in signal.reason


def test_dst_session_calendar():
    session = _session_date(2026, 3, 9)  # US DST starts second Sunday March 2026
    opens = expected_opening_range_candle_opens(session)
    first_local = opens[0].astimezone(ET)
    assert first_local.hour == 9 and first_local.minute == 30
    assert is_opening_range_complete(
        datetime(2026, 3, 9, 10, 0, tzinfo=ET).astimezone(timezone.utc)
    )


def test_robot_a_regression_unchanged():
    gtp = GoldTrendPullbackV1()
    assert gtp.strategy_id() == "gold-trend-pullback"
    ctx = StrategyContext("xau", "1h", {})
    signal = gtp.evaluate([], ctx)
    assert signal.action == SignalAction.HOLD
    assert "INSUFFICIENT_DATA" in signal.reason


def test_next_candle_open_execution_scheduled():
    from quantara_engine.pipeline.candle_processor import CandleProcessor

    signal_candle = _orb_candle(datetime(2026, 3, 10, 10, 0, tzinfo=ET), "500", "501.5", "499.8", "501.0")
    exec_candle = _orb_candle(datetime(2026, 3, 10, 10, 5, tzinfo=ET), "501", "502", "500.5", "501.5")
    proc = CandleProcessor(
        portfolio_state=MagicMock(),
        strategy_instance=MagicMock(),
        instrument=MagicMock(),
        risk_profile=MagicMock(),
        broker=MagicMock(),
    )
    proc.all_candles = [signal_candle, exec_candle]
    exec_ts = proc._next_execution_timestamp(signal_candle, 0)
    assert exec_ts == exec_candle.timestamp
    assert exec_ts > signal_candle.timestamp


def test_missed_execution_window_rejected():
    from quantara_engine.domain.types import Direction, IntentStatus, OrderIntent, new_id
    from quantara_engine.pipeline.candle_processor import intent_execution_allowed

    signal_ts = datetime(2026, 3, 10, 10, 0, tzinfo=ET).astimezone(timezone.utc)
    exec_ts = datetime(2026, 3, 10, 10, 5, tzinfo=ET).astimezone(timezone.utc)
    late_now = datetime(2026, 3, 10, 12, 0, tzinfo=ET).astimezone(timezone.utc)
    intent = OrderIntent(
        id=new_id(),
        signal_id=new_id(),
        strategy_instance_id="si-orb",
        portfolio_id="p-orb",
        direction=Direction.LONG,
        quantity=Decimal("1"),
        stop_loss=Decimal("498"),
        take_profit=Decimal("506"),
        target_risk_amount=Decimal("20"),
        actual_risk_amount=Decimal("20"),
        signal_candle_timestamp=signal_ts,
        execution_candle_timestamp=exec_ts,
        risk_profile_id="rp1",
        status=IntentStatus.PENDING_EXECUTION,
    )
    candle = _orb_candle(datetime(2026, 3, 10, 10, 5, tzinfo=ET), "501", "502", "500.5", "501.5")
    allowed, reason = intent_execution_allowed(intent, candle=candle, now=late_now)
    assert not allowed
    assert reason is not None
    assert "stale_signal_age" in reason


def test_historical_backtest_does_not_open_paper_position():
    from quantara_engine.domain.types import DecisionType, Signal, SignalAction
    from quantara_engine.pipeline.candle_processor import CandleProcessor

    session = _session_date(2026, 3, 10)
    candles = _atr_warmup(session) + _opening_range_candles(session, high="500", low="498")
    breakout = _orb_candle(datetime(2026, 3, 10, 10, 0, tzinfo=ET), "500", "501.5", "499.8", "501.2")
    proc = CandleProcessor(
        portfolio_state=MagicMock(open_positions=MagicMock(return_value=[]), portfolio=MagicMock(status="active")),
        strategy_instance=MagicMock(id="si-orb", strategy_slug="opening-range-breakout"),
        instrument=MagicMock(symbol="SPY", id="spy-id"),
        risk_profile=MagicMock(),
        broker=MagicMock(),
        allow_live_execution=False,
    )
    proc.all_candles = candles + [breakout]
    proc.process_candle(len(proc.all_candles) - 1, shared_signal=Signal(action=SignalAction.BUY, reason="test"))
    assert proc.state.open_positions() == [] or len(proc.state.open_positions()) == 0
    assert any(d.decision_type == DecisionType.BUY_SIGNAL for d in proc.decisions)


def test_atr_sl_tp_2r_defaults():
    strategy = OpeningRangeBreakoutV1()
    session = _session_date(2026, 3, 10)
    candles = _atr_warmup(session) + _opening_range_candles(session, high="500", low="498")
    breakout = _orb_candle(datetime(2026, 3, 10, 10, 0, tzinfo=ET), "500", "501.5", "499.8", "501.0")
    ctx = StrategyContext("spy-id", "5m", {}, runtime={"trades_today": 0})
    signal = strategy.evaluate(candles + [breakout], ctx)
    assert signal.suggested_sl is not None
    assert signal.suggested_tp is not None
    risk = signal.suggested_tp - breakout.close
    stop = breakout.close - signal.suggested_sl
    assert float(risk / stop) == pytest.approx(2.0, rel=0.01)
