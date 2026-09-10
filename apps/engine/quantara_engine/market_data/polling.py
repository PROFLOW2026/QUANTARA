"""Practical polling helpers for candle-close ingestion."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

BAR_MINUTES: dict[str, int] = {
    "5m": 5,
    "15m": 15,
    "1h": 60,
}

TIMEFRAMES: tuple[str, ...] = ("5m", "15m", "1h")

# Provider ingestion uses canonical 5m only; higher timeframes are derived locally.
PROVIDER_TIMEFRAME = "5m"
DERIVED_TIMEFRAMES: tuple[str, ...] = ("15m", "1h")

# Minimum bars required by Gold Trend Pullback v1
STRATEGY_MIN_CANDLES = 200

# Initial bootstrap size per timeframe (one API call each)
BOOTSTRAP_OUTPUT_SIZE = 250

# 5m bars needed so derived 1h can reach STRATEGY_MIN_CANDLES (200 × 12 five-minute bars).
BOOTSTRAP_MIN_5M_BARS = STRATEGY_MIN_CANDLES * (BAR_MINUTES["1h"] // BAR_MINUTES["5m"])


def timeframe_minutes(timeframe: str) -> int:
    return BAR_MINUTES.get(timeframe, 60)


def should_fetch_timeframe(
    timeframe: str,
    last_timestamp: datetime | None,
    now: datetime | None = None,
) -> bool:
    """Fetch when no data exists or a new bar should have closed."""
    now = now or datetime.now(timezone.utc)
    if last_timestamp is None:
        return True
    if last_timestamp.tzinfo is None:
        last_timestamp = last_timestamp.replace(tzinfo=timezone.utc)
    elapsed_min = (now - last_timestamp).total_seconds() / 60
    return elapsed_min >= timeframe_minutes(timeframe)


def bootstrap_start(timeframe: str, end: datetime, bars: int = BOOTSTRAP_OUTPUT_SIZE) -> datetime:
    return end - timedelta(minutes=timeframe_minutes(timeframe) * bars)


def bar_close_timestamp(candle_timestamp: datetime, timeframe: str) -> datetime:
    """When a bar closes — candle timestamps represent bar OPEN time."""
    if candle_timestamp.tzinfo is None:
        candle_timestamp = candle_timestamp.replace(tzinfo=timezone.utc)
    return candle_timestamp + timedelta(minutes=timeframe_minutes(timeframe))


def is_bar_complete(candle_timestamp: datetime, timeframe: str, now: datetime | None = None) -> bool:
    """True when the bar period has fully elapsed (closed candle only)."""
    now = now or datetime.now(timezone.utc)
    return bar_close_timestamp(candle_timestamp, timeframe) <= now


# Provider lag tolerance on top of one full bar period.
PROVIDER_LATENCY_SLACK_MINUTES = 15


def bar_staleness_minutes(
    candle_timestamp: datetime,
    timeframe: str,
    now: datetime | None = None,
) -> float:
    """Minutes since the latest stored bar closed (not since bar open)."""
    now = now or datetime.now(timezone.utc)
    close_ts = bar_close_timestamp(candle_timestamp, timeframe)
    return (now - close_ts).total_seconds() / 60


def max_staleness_minutes(timeframe: str) -> float:
    """Maximum acceptable age after bar close before data is stale."""
    return timeframe_minutes(timeframe) + PROVIDER_LATENCY_SLACK_MINUTES


def is_market_data_fresh(
    candle_timestamp: datetime,
    timeframe: str,
    now: datetime | None = None,
) -> bool:
    """True when the latest stored bar is the expected completed bar for its timeframe."""
    now = now or datetime.now(timezone.utc)
    return bar_staleness_minutes(candle_timestamp, timeframe, now) < max_staleness_minutes(timeframe)
