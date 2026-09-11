"""Short-lived read cache for Home dashboard candle aggregates."""

from __future__ import annotations

import time
from threading import Lock
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from quantara_engine.persistence.store import TradingStore

_CACHE_TTL_SECONDS = 45.0
_lock = Lock()
_candle_cache: dict[tuple[str, ...], tuple[float, dict[str, Any]]] = {}


def dashboard_candle_bundle(
    store: TradingStore,
    instrument_ids: list[str],
) -> dict[str, Any]:
    """Batch candle counts/timestamps/closes with a brief TTL cache."""
    key = tuple(sorted(instrument_ids))
    now = time.monotonic()
    with _lock:
        hit = _candle_cache.get(key)
        if hit and now - hit[0] < _CACHE_TTL_SECONDS:
            return hit[1]

    from quantara_engine.persistence.batch_summary import (
        batch_candle_counts,
        batch_latest_candle_closes,
        batch_latest_candle_timestamps,
    )

    bundle = {
        "counts": batch_candle_counts(store, instrument_ids),
        "last_candles": batch_latest_candle_timestamps(store, instrument_ids, "5m"),
        "latest_closes": batch_latest_candle_closes(store, instrument_ids, "5m"),
    }
    with _lock:
        _candle_cache[key] = (now, bundle)
    return bundle
