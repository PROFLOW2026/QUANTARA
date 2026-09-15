"""Short-lived read cache for Home dashboard aggregates (UI-only, not worker decisions)."""

from __future__ import annotations

import threading
import time
from threading import Lock
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from quantara_engine.persistence.store import TradingStore

_CANDLE_TTL_SECONDS = 45.0
_HOME_TTL_SECONDS = 45.0
_lock = Lock()
_candle_cache: dict[tuple[str, ...], tuple[float, dict[str, Any]]] = {}
_home_cache: dict[str, tuple[float, Any]] = {}
_home_loading: dict[str, Any] = {}


def dashboard_candle_bundle(
    store: TradingStore,
    instrument_ids: list[str],
) -> dict[str, Any]:
    """Batch candle counts/timestamps/closes with a brief TTL cache."""
    key = tuple(sorted(instrument_ids))
    now = time.monotonic()
    with _lock:
        hit = _candle_cache.get(key)
        if hit and now - hit[0] < _CANDLE_TTL_SECONDS:
            return hit[1]

    from quantara_engine.persistence.batch_summary import (
        batch_candle_counts,
        batch_latest_candle_closes,
        batch_latest_candle_timestamps,
    )

    bundle = {
        "counts": batch_candle_counts(store, instrument_ids),
        "last_candles": batch_latest_candle_timestamps(store, instrument_ids, "5m"),
        "last_1m_candles": batch_latest_candle_timestamps(store, instrument_ids, "1m"),
        "latest_closes": batch_latest_candle_closes(store, instrument_ids, "5m"),
    }
    with _lock:
        _candle_cache[key] = (now, bundle)
    return bundle


def cached_home_payload(cache_key: str, loader: Callable[[], Any]) -> Any:
    """TTL cache for read-only Home dashboard endpoint payloads."""
    now = time.monotonic()
    with _lock:
        hit = _home_cache.get(cache_key)
        if hit and now - hit[0] < _HOME_TTL_SECONDS:
            return hit[1]
        wait_event = _home_loading.get(cache_key)
        if wait_event is None:
            wait_event = threading.Event()
            _home_loading[cache_key] = wait_event
            leader = True
        else:
            leader = False

    if not leader:
        wait_event.wait(timeout=_HOME_TTL_SECONDS + 10.0)
        with _lock:
            hit = _home_cache.get(cache_key)
            if hit:
                return hit[1]
        payload = loader()
        with _lock:
            _home_cache[cache_key] = (time.monotonic(), payload)
        return payload

    try:
        payload = loader()
        with _lock:
            _home_cache[cache_key] = (time.monotonic(), payload)
        return payload
    finally:
        with _lock:
            event = _home_loading.pop(cache_key, None)
        if event is not None:
            event.set()


def clear_dashboard_caches() -> None:
    """Test helper."""
    with _lock:
        _candle_cache.clear()
        _home_cache.clear()
        _home_loading.clear()
