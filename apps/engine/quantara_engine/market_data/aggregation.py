"""Derive higher-timeframe candles from canonical 5m bars (and 1m→5m)."""

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
CANONICAL_FROM_1M: str = "5m"
_1M_PER_5M = timeframe_minutes("5m") // timeframe_minutes("1m")


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


def _expected_1m_timestamps(bucket: datetime, count: int = _1M_PER_5M) -> list[datetime]:
    return [bucket + timedelta(minutes=i) for i in range(count)]


def _aggregate_ohlcv(
    components: list[Candle],
    *,
    timeframe: str,
    bucket: datetime,
    source: str,
) -> Candle:
    volumes = [c.volume for c in components if c.volume is not None]
    volume: Decimal | None
    if volumes:
        volume = sum(volumes, start=Decimal("0"))
    else:
        volume = None
    return Candle(
        instrument_id=components[0].instrument_id,
        timeframe=timeframe,
        timestamp=bucket,
        open=components[0].open,
        high=max(c.high for c in components),
        low=min(c.low for c in components),
        close=components[-1].close,
        volume=volume,
        source=source,
        is_complete=True,
    )


def _component_source(components: list[Candle], *, default: str = "aggregated") -> str:
    sources = {str(c.source) for c in components if c.source}
    if len(sources) == 1:
        return next(iter(sources))
    return default


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

        derived.append(
            _aggregate_ohlcv(
                components,
                timeframe=target_timeframe,
                bucket=bucket,
                source=source,
            )
        )

    return derived


def aggregate_from_1m(
    base_candles: list[Candle],
    *,
    source: str | None = None,
    session_mode: str = "utc",
) -> list[Candle]:
    """
    Build completed canonical 5m candles from completed 1m inputs.

    Requires exactly five consecutive completed 1m bars on exact UTC (or US RTH)
    boundaries. Partial buckets are never emitted (no lookahead).
    """
    by_bucket: dict[datetime, list[Candle]] = {}

    for candle in base_candles:
        if candle.timeframe != "1m" or not candle.is_complete:
            continue
        if bucket_start(candle.timestamp, "1m") != candle.timestamp:
            continue
        if session_mode == "us_rth":
            bucket = us_rth_bucket_start(candle.timestamp, "5m")
            if bucket is None:
                continue
        else:
            bucket = bucket_start(candle.timestamp, "5m")
        by_bucket.setdefault(bucket, []).append(candle)

    derived: list[Candle] = []
    for bucket in sorted(by_bucket):
        components = sorted(by_bucket[bucket], key=lambda c: c.timestamp)
        # Dedupe same-minute duplicates (multi-source 1m rows).
        deduped: dict[datetime, Candle] = {}
        for c in components:
            deduped[c.timestamp] = c
        components = [deduped[ts] for ts in sorted(deduped)]
        if len(components) != _1M_PER_5M:
            continue
        expected = _expected_1m_timestamps(bucket, _1M_PER_5M)
        if [c.timestamp for c in components] != expected:
            continue
        bar_source = source if source is not None else _component_source(components)
        derived.append(
            _aggregate_ohlcv(
                components,
                timeframe=CANONICAL_FROM_1M,
                bucket=bucket,
                source=bar_source,
            )
        )

    return derived


def incremental_1m_source_limit(new_1m_count: int) -> int:
    """Minimal 1m lookback when deriving only 5m buckets touched by new 1m bars."""
    return _1M_PER_5M * 3 + max(new_1m_count, 1) + _1M_PER_5M


def affected_5m_buckets_from_1m(
    timestamps: list[datetime],
    *,
    session_mode: str,
) -> set[datetime]:
    buckets: set[datetime] = set()
    for ts in timestamps:
        if session_mode == "us_rth":
            bucket = us_rth_bucket_start(ts, "5m")
        else:
            bucket = bucket_start(ts, "5m")
        if bucket is not None:
            buckets.add(bucket)
    return buckets


def incremental_derive_from_1m(
    base_candles: list[Candle],
    new_1m_timestamps: list[datetime],
    *,
    session_mode: str = "utc",
    source: str | None = None,
) -> list[Candle]:
    """Derive only completed 5m buckets affected by newly upserted 1m candles."""
    if not base_candles or not new_1m_timestamps:
        return []
    target_buckets = affected_5m_buckets_from_1m(
        new_1m_timestamps,
        session_mode=session_mode,
    )
    if not target_buckets:
        return []
    return [
        candle
        for candle in aggregate_from_1m(
            base_candles,
            session_mode=session_mode,
            source=source,
        )
        if candle.timestamp in target_buckets
    ]


def aggregation_lookback_bars(target_timeframe: str, extra_buckets: int = 2) -> int:
    """How many 5m bars to load when recomputing derived candles."""
    target_minutes = BAR_MINUTES[target_timeframe]
    base_minutes = BAR_MINUTES["5m"]
    components = target_minutes // base_minutes
    return components * (1 + extra_buckets)


def incremental_source_limit(new_5m_count: int) -> int:
    """Minimal 5m lookback when deriving only buckets touched by new bars."""
    max_components = max(
        timeframe_minutes(tf) // timeframe_minutes("5m") for tf in DERIVED_FROM_5M
    )
    window = max(aggregation_lookback_bars(tf) for tf in DERIVED_FROM_5M)
    return window + max(new_5m_count, 1) + max_components


def affected_derived_buckets(
    timestamps: list[datetime],
    target_timeframe: str,
    *,
    session_mode: str,
) -> set[datetime]:
    buckets: set[datetime] = set()
    for ts in timestamps:
        if session_mode == "us_rth":
            bucket = us_rth_bucket_start(ts, target_timeframe)
        else:
            bucket = bucket_start(ts, target_timeframe)
        if bucket is not None:
            buckets.add(bucket)
    return buckets


def incremental_derive_from_5m(
    base_candles: list[Candle],
    new_5m_timestamps: list[datetime],
    *,
    session_mode: str = "utc",
    source: str = "aggregated",
) -> list[Candle]:
    """Derive only 15m/1h buckets affected by newly upserted 5m candles."""
    if not base_candles or not new_5m_timestamps:
        return []

    derived: list[Candle] = []
    for target_tf in DERIVED_FROM_5M:
        target_buckets = affected_derived_buckets(
            new_5m_timestamps,
            target_tf,
            session_mode=session_mode,
        )
        if not target_buckets:
            continue
        for candle in aggregate_from_5m(
            base_candles,
            target_tf,
            session_mode=session_mode,
            source=source,
        ):
            if candle.timestamp in target_buckets:
                derived.append(candle)
    return derived


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
