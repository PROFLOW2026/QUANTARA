"""Gap and open-first exit trigger tests."""

from decimal import Decimal

from quantara_engine.domain.types import Candle, Direction, ExitReason, Position, PositionStatus
from quantara_engine.execution.exit_triggers import detect_exit_trigger


def _pos(direction: str, sl: str, tp: str | None = None) -> Position:
    return Position(
        id="p1",
        portfolio_id="pf1",
        strategy_instance_id="si1",
        instrument_id="x",
        direction=Direction.LONG if direction == "long" else Direction.SHORT,
        quantity=Decimal("1"),
        entry_price=Decimal("360"),
        stop_loss=Decimal(sl),
        take_profit=Decimal(tp) if tp else None,
        current_price=Decimal("360"),
        status=PositionStatus.OPEN,
    )


def _candle(open_: str, high: str, low: str, close: str) -> Candle:
    return Candle(
        instrument_id="x",
        timeframe="5m",
        timestamp=__import__("datetime").datetime(2026, 1, 2, 14, 0, tzinfo=__import__("datetime").timezone.utc),
        open=Decimal(open_),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
        volume=Decimal("1"),
    )


def test_long_gap_through_sl_uses_open():
    pos = _pos("long", "350")
    candle = _candle("340", "345", "335", "342")
    reason, price = detect_exit_trigger(pos, candle)
    assert reason == ExitReason.SL
    assert price == Decimal("340")


def test_short_gap_through_sl_uses_open():
    pos = _pos("short", "370")
    candle = _candle("385", "390", "380", "388")
    reason, price = detect_exit_trigger(pos, candle)
    assert reason == ExitReason.SL
    assert price == Decimal("385")


def test_long_gap_through_tp_uses_open():
    pos = _pos("long", "350", "380")
    candle = _candle("390", "395", "388", "392")
    reason, price = detect_exit_trigger(pos, candle)
    assert reason == ExitReason.TP
    assert price == Decimal("390")


def test_short_gap_through_tp_uses_open():
    pos = _pos("short", "370", "340")
    candle = _candle("330", "335", "325", "332")
    reason, price = detect_exit_trigger(pos, candle)
    assert reason == ExitReason.TP
    assert price == Decimal("330")


def test_intrabar_sl_when_open_did_not_cross():
    pos = _pos("long", "355")
    candle = _candle("360", "362", "354", "358")
    reason, price = detect_exit_trigger(pos, candle)
    assert reason == ExitReason.SL
    assert price == Decimal("355")


def test_same_bar_sl_and_tp_sl_first():
    pos = _pos("long", "355", "365")
    candle = _candle("360", "366", "354", "360")
    reason, _ = detect_exit_trigger(pos, candle)
    assert reason == ExitReason.SL
