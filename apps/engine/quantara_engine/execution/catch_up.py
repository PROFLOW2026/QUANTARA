"""Sequential candle catch-up helpers for live paper workers."""

from __future__ import annotations

from datetime import datetime

from quantara_engine.domain.types import Candle
from quantara_engine.market_data.polling import is_bar_complete


def _effective_competition_floor(competition_floor: datetime | None) -> datetime | None:
    return competition_floor if isinstance(competition_floor, datetime) else None


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
    """Raw candle-gap count (diagnostics only — may include non-actionable gaps)."""
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


def list_actionable_catchup_indices(
    candles: list[Candle],
    timeframe: str,
    processed_timestamps: set[datetime],
    now: datetime,
    *,
    competition_floor: datetime | None = None,
) -> list[int]:
    """Catch-up indices using the same rules as the strategy worker."""
    indices = list_catchup_candle_indices(
        candles,
        timeframe,
        last_processed=None,
        now=now,
        processed_timestamps=processed_timestamps,
    )
    floor = _effective_competition_floor(competition_floor)
    if floor is not None:
        indices = [i for i in indices if candles[i].timestamp >= floor]
    return indices


def compute_actionable_backlog_status(
    candles: list[Candle],
    timeframe: str,
    processed_timestamps: set[datetime],
    now: datetime,
    *,
    competition_floor: datetime | None = None,
    eligible: bool = True,
) -> dict:
    """Actionable pending work aligned with strategy runner scheduling."""
    last_processed = max(processed_timestamps) if processed_timestamps else None
    last_completed: datetime | None = None
    for candle in candles:
        if is_bar_complete(candle.timestamp, timeframe, now):
            last_completed = candle.timestamp

    if not eligible:
        return {
            "last_completed_candle": last_completed.isoformat() if last_completed else None,
            "last_processed_candle": last_processed.isoformat() if last_processed else None,
            "backlog": 0,
            "live_actionable": 0,
            "historical_actionable": 0,
            "status": "healthy",
        }

    indices = list_actionable_catchup_indices(
        candles,
        timeframe,
        processed_timestamps,
        now,
        competition_floor=competition_floor,
    )
    total = len(indices)
    live_actionable = 1 if total > 0 else 0
    historical_actionable = max(0, total - 1)

    return {
        "last_completed_candle": last_completed.isoformat() if last_completed else None,
        "last_processed_candle": last_processed.isoformat() if last_processed else None,
        "backlog": total,
        "live_actionable": live_actionable,
        "historical_actionable": historical_actionable,
        "status": "catching_up" if total > 0 else "healthy",
    }


def aggregate_actionable_backlog(timeframe_status: dict[str, dict]) -> tuple[int, int, int]:
    """Sum live/historical actionable units across timeframe buckets."""
    live = 0
    historical = 0
    for status in timeframe_status.values():
        if not isinstance(status, dict):
            continue
        live += int(status.get("live_actionable") or 0)
        historical += int(status.get("historical_actionable") or 0)
    return live, historical, live + historical
