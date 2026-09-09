"""Sequential candle catch-up helpers for live paper workers."""

from __future__ import annotations

from datetime import datetime

from quantara_engine.domain.types import Candle
from quantara_engine.market_data.polling import is_bar_complete


def last_fully_processed_timestamp(
    candle_timestamps: list[datetime],
    required_instance_count: int,
) -> datetime | None:
    """Latest candle timestamp where every instance in the group has a decision."""
    if not candle_timestamps or required_instance_count <= 0:
        return None
    from collections import Counter

    counts = Counter(candle_timestamps)
    fully_processed = [ts for ts, cnt in counts.items() if cnt >= required_instance_count]
    return max(fully_processed) if fully_processed else None


def list_catchup_candle_indices(
    candles: list[Candle],
    timeframe: str,
    *,
    last_processed: datetime | None,
    now: datetime,
    already_processed_fn=None,
    processed_timestamps: set[datetime] | None = None,
) -> list[int]:
    """Indices of completed candles that still need group processing, oldest first."""
    indices: list[int] = []
    for index, candle in enumerate(candles):
        if not is_bar_complete(candle.timestamp, timeframe, now):
            continue
        if last_processed is not None and candle.timestamp <= last_processed:
            continue
        if processed_timestamps is not None:
            if candle.timestamp in processed_timestamps:
                continue
        elif already_processed_fn is not None and already_processed_fn(candle.timestamp):
            continue
        indices.append(index)
    return indices


def compute_backlog_status(
    candles: list[Candle],
    timeframe: str,
    *,
    last_processed: datetime | None,
    now: datetime,
) -> dict:
    """Lightweight backlog summary for worker health."""
    last_completed: datetime | None = None
    backlog = 0
    for candle in candles:
        if not is_bar_complete(candle.timestamp, timeframe, now):
            continue
        last_completed = candle.timestamp
        if last_processed is None or candle.timestamp > last_processed:
            backlog += 1
    return {
        "last_completed_candle": last_completed.isoformat() if last_completed else None,
        "last_processed_candle": last_processed.isoformat() if last_processed else None,
        "backlog": backlog,
        "status": "catching_up" if backlog > 0 else "healthy",
    }
