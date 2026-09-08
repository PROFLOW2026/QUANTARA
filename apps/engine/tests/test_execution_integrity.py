"""Focused tests for paper execution integrity (catch-up, SL/TP)."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from quantara_engine.domain.types import Candle, Direction, ExitReason, Position, PositionStatus
from quantara_engine.execution.catch_up import list_catchup_candle_indices
from quantara_engine.execution.exit_triggers import detect_exit_trigger, find_first_exit_candle


def _candle(ts: str, o: str, h: str, l: str, c: str) -> Candle:
    return Candle(
        instrument_id="inst",
        timeframe="5m",
        timestamp=datetime.fromisoformat(ts).replace(tzinfo=timezone.utc),
        open=Decimal(o),
        high=Decimal(h),
        low=Decimal(l),
        close=Decimal(c),
    )


def _long_position(sl: str = "100", tp: str = "110") -> Position:
    return Position(
        id="p1",
        portfolio_id="pf",
        strategy_instance_id="si",
        instrument_id="inst",
        direction=Direction.LONG,
        quantity=Decimal("1"),
        entry_price=Decimal("105"),
        current_price=Decimal("105"),
        stop_loss=Decimal(sl),
        take_profit=Decimal(tp),
        status=PositionStatus.OPEN,
        opened_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


def test_sl_wins_when_both_touched_same_candle() -> None:
    pos = _long_position(sl="100", tp="110")
    candle = _candle("2026-01-01T00:05:00", "105", "112", "99", "105")
    trigger = detect_exit_trigger(pos, candle)
    assert trigger is not None
    assert trigger[0] == ExitReason.SL
    assert trigger[1] == Decimal("100")


def test_tp_long_when_only_tp_touched() -> None:
    pos = _long_position(sl="95", tp="110")
    candle = _candle("2026-01-01T00:05:00", "105", "111", "100", "108")
    trigger = detect_exit_trigger(pos, candle)
    assert trigger == (ExitReason.TP, Decimal("110"))


def test_sl_short() -> None:
    pos = Position(
        id="p1",
        portfolio_id="pf",
        strategy_instance_id="si",
        instrument_id="inst",
        direction=Direction.SHORT,
        quantity=Decimal("1"),
        entry_price=Decimal("105"),
        current_price=Decimal("105"),
        stop_loss=Decimal("110"),
        take_profit=Decimal("95"),
        status=PositionStatus.OPEN,
        opened_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    candle = _candle("2026-01-01T00:05:00", "105", "111", "100", "108")
    trigger = detect_exit_trigger(pos, candle)
    assert trigger == (ExitReason.SL, Decimal("110"))


def test_tp_short() -> None:
    pos = Position(
        id="p1",
        portfolio_id="pf",
        strategy_instance_id="si",
        instrument_id="inst",
        direction=Direction.SHORT,
        quantity=Decimal("1"),
        entry_price=Decimal("105"),
        current_price=Decimal("105"),
        stop_loss=Decimal("115"),
        take_profit=Decimal("98"),
        status=PositionStatus.OPEN,
        opened_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    candle = _candle("2026-01-01T00:05:00", "105", "106", "97", "100")
    trigger = detect_exit_trigger(pos, candle)
    assert trigger == (ExitReason.TP, Decimal("98"))


def test_find_first_exit_candle_after_entry() -> None:
    pos = _long_position(sl="4406", tp="4420")
    candles = [
        _candle("2026-01-01T00:00:00", "4410", "4412", "4409", "4411"),
        _candle("2026-01-01T00:05:00", "4411", "4412", "4405", "4407"),
        _candle("2026-01-01T00:10:00", "4407", "4408", "4400", "4401"),
    ]
    hit = find_first_exit_candle(pos, candles, after_timestamp=pos.opened_at)
    assert hit is not None
    exit_candle, reason, price = hit
    assert reason == ExitReason.SL
    assert exit_candle.timestamp == candles[1].timestamp
    assert price == Decimal("4406")


def test_catchup_indices_skips_processed_and_incomplete() -> None:
    candles = [
        _candle("2026-01-01T00:00:00", "1", "2", "0.5", "1.5"),
        _candle("2026-01-01T00:05:00", "1.5", "2", "1", "1.8"),
        _candle("2026-01-01T00:10:00", "1.8", "2", "1.5", "1.9"),
    ]
    now = datetime(2026, 1, 1, 0, 11, tzinfo=timezone.utc)
    processed = {candles[0].timestamp}

    indices = list_catchup_candle_indices(
        candles,
        "5m",
        last_processed=candles[0].timestamp,
        now=now,
        already_processed_fn=lambda ts: ts in processed,
    )
    assert indices == [1]
