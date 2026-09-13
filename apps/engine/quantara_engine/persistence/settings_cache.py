"""Process-level TTL cache for settings reads (Home dashboard hot path)."""

from __future__ import annotations

import time
from threading import Lock
from typing import Any

_SETTINGS_TTL_SEC = 10.0
_lock = Lock()
_cache: tuple[float, dict[str, Any]] | None = None


def load_process_settings_cache() -> dict[str, Any] | None:
    now = time.monotonic()
    with _lock:
        if _cache and now - _cache[0] < _SETTINGS_TTL_SEC:
            return dict(_cache[1])
    return None


def store_process_settings_cache(data: dict[str, Any], *, now: float | None = None) -> None:
    ts = time.monotonic() if now is None else now
    with _lock:
        global _cache
        _cache = (ts, dict(data))


def clear_process_settings_cache() -> None:
    with _lock:
        global _cache
        _cache = None
