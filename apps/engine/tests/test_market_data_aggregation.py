"""Tests for UTC 5m → 15m/1h candle aggregation."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from quantara_engine.domain.types import Candle
from quantara_engine.market_data.aggregation import aggregate_from_5m, bucket_start


def _bar(ts: str, o: str, h: str, l: str, c: str) -> Candle:
    return Candle(
        instrument_id="inst-1",
        timeframe="5m",
        timestamp=datetime.fromisoformat(ts).replace(tzinfo=timezone.utc),
        open=Decimal(o),
        high=Decimal(h),
        low=Decimal(l),
        close=Decimal(c),
        source="test",
        is_complete=True,
    )


def test_bucket_start_utc_alignment():
    ts = datetime(2026, 9, 8, 10, 17, 0, tzinfo=timezone.utc)
    assert bucket_start(ts, "15m") == datetime(2026, 9, 8, 10, 15, tzinfo=timezone.utc)
    assert bucket_start(ts, "1h") == datetime(2026, 9, 8, 10, 0, tzinfo=timezone.utc)


def test_aggregate_15m_requires_three_consecutive_5m_bars():
    base = [
        _bar("2026-09-08T10:15:00+00:00", "1", "2", "0.5", "1.5"),
        _bar("2026-09-08T10:20:00+00:00", "1.5", "2.5", "1.0", "2.0"),
    ]
    assert aggregate_from_5m(base, "15m") == []

    base.append(_bar("2026-09-08T10:25:00+00:00", "2.0", "3.0", "1.5", "2.5"))
    derived = aggregate_from_5m(base, "15m")
    assert len(derived) == 1
    bar = derived[0]
    assert bar.timeframe == "15m"
    assert bar.timestamp == datetime(2026, 9, 8, 10, 15, tzinfo=timezone.utc)
    assert bar.open == Decimal("1")
    assert bar.high == Decimal("3.0")
    assert bar.low == Decimal("0.5")
    assert bar.close == Decimal("2.5")
    assert bar.is_complete is True


def test_aggregate_1h_requires_twelve_consecutive_5m_bars():
    start = datetime(2026, 9, 8, 10, 0, tzinfo=timezone.utc)
    bars = []
    for i in range(11):
        ts = start.replace(minute=i * 5)
        bars.append(
            _bar(
                ts.isoformat(),
                str(100 + i),
                str(101 + i),
                str(99 + i),
                str(100.5 + i),
            )
        )
    assert aggregate_from_5m(bars, "1h") == []

    ts = start.replace(minute=55)
    bars.append(_bar(ts.isoformat(), "111", "112", "110", "111.5"))
    derived = aggregate_from_5m(bars, "1h")
    assert len(derived) == 1
    assert derived[0].timestamp == start
    assert derived[0].close == Decimal("111.5")


def test_aggregate_skips_gap_in_5m_sequence():
    bars = [
        _bar("2026-09-08T10:15:00+00:00", "1", "2", "0.5", "1.5"),
        _bar("2026-09-08T10:25:00+00:00", "2.0", "3.0", "1.5", "2.5"),
        _bar("2026-09-08T10:30:00+00:00", "2.5", "3.5", "2.0", "3.0"),
    ]
    assert aggregate_from_5m(bars, "15m") == []
