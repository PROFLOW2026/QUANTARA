"""Derive higher-timeframe candles from canonical 5m bars."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from quantara_engine.domain.types import Candle
from quantara_engine.market_data.polling import BAR_MINUTES, timeframe_minutes
from quantara_engine.market_data.sessions import (
    expected_us_rth_5m_timestamps,
    us_rth_bucket_start,
)

DERIVED_FROM_5M: tuple[str, ...] = ("15m", "1h")


def bucket_start(timestamp: datetime, timeframe: str) -> datetime:
    """Floor a UTC timestamp to the open of its timeframe bucket."""
    ts = timestamp.astimezone(timezone.utc) if timestamp.tzinfo else timestamp.replace(tzinfo=timezone.utc)
    minutes = timeframe_minutes(timeframe)
    epoch = int(ts.timestamp())
    bucket_seconds = minutes * 60
    floored = epoch - (epoch % bucket_seconds)
    return datetime.fromtimestamp(floored, tz=timezone.utc)


def _expected_5m_timestamps(bucket: datetime, count: int) -> list[datetime]:
    base = timeframe_minutes("5m")
    return [bucket + timedelta(minutes=base * i) for i in range(count)]


def aggregate_from_5m(
    base_candles: list[Candle],
    target_timeframe: str,
    *,
    source: str = "aggregated",
    session_mode: str = "utc",
) -> list[Candle]:
    """
    Build completed higher-timeframe candles from 5m inputs.

    session_mode:
      - utc: standard UTC bucket alignment (FX, crypto)
      - us_rth: regular US session buckets (09:30–16:00 America/New_York)
    """
    if target_timeframe not in DERIVED_FROM_5M:
        raise ValueError(f"Unsupported derived timeframe: {target_timeframe}")

    base_minutes = timeframe_minutes("5m")
    target_minutes = timeframe_minutes(target_timeframe)
    if target_minutes % base_minutes != 0:
        raise ValueError(f"{target_timeframe} is not divisible from 5m")

    component_count = target_minutes // base_minutes
    by_bucket: dict[datetime, list[Candle]] = {}

    for candle in base_candles:
        if candle.timeframe != "5m" or not candle.is_complete:
            continue
        if session_mode == "us_rth":
            if bucket_start(candle.timestamp, "5m") != candle.timestamp:
                continue
            bucket = us_rth_bucket_start(candle.timestamp, target_timeframe)
            if bucket is None:
                continue
        else:
            if bucket_start(candle.timestamp, "5m") != candle.timestamp:
                continue
            bucket = bucket_start(candle.timestamp, target_timeframe)
        by_bucket.setdefault(bucket, []).append(candle)

    derived: list[Candle] = []
    for bucket in sorted(by_bucket):
        components = sorted(by_bucket[bucket], key=lambda c: c.timestamp)
        if len(components) != component_count:
            continue
        expected = (
            expected_us_rth_5m_timestamps(bucket, component_count)
            if session_mode == "us_rth"
            else _expected_5m_timestamps(bucket, component_count)
        )
        if [c.timestamp for c in components] != expected:
            continue

        volumes = [c.volume for c in components if c.volume is not None]
        volume: Decimal | None
        if volumes:
            volume = sum(volumes, start=Decimal("0"))
        else:
            volume = None

        derived.append(
            Candle(
                instrument_id=components[0].instrument_id,
                timeframe=target_timeframe,
                timestamp=bucket,
                open=components[0].open,
                high=max(c.high for c in components),
                low=min(c.low for c in components),
                close=components[-1].close,
                volume=volume,
                source=source,
                is_complete=True,
            )
        )

    return derived


def aggregation_lookback_bars(target_timeframe: str, extra_buckets: int = 2) -> int:
    """How many 5m bars to load when recomputing derived candles."""
    target_minutes = BAR_MINUTES[target_timeframe]
    base_minutes = BAR_MINUTES["5m"]
    components = target_minutes // base_minutes
    return components * (1 + extra_buckets)


def derivation_source_limit(stored_5m_count: int) -> int:
    """
    Load enough completed 5m bars to rebuild up to STRATEGY_MIN_CANDLES derived bars.

    1h needs the deepest window (200 buckets × 12 five-minute bars).
    """
    from quantara_engine.market_data.polling import STRATEGY_MIN_CANDLES, timeframe_minutes

    if stored_5m_count <= 0:
        return 0
    max_components = max(
        timeframe_minutes(tf) // timeframe_minutes("5m") for tf in DERIVED_FROM_5M
    )
    needed = STRATEGY_MIN_CANDLES * max_components + max_components
    incremental = max(aggregation_lookback_bars(tf) for tf in DERIVED_FROM_5M)
    return min(stored_5m_count, max(needed, incremental))
