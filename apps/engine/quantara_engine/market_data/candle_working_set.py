"""Worker-process candle working set — bounded warm load + incremental tail fetch."""

from __future__ import annotations

from datetime import datetime

from quantara_engine.domain.types import Candle
from quantara_engine.market_data.polling import STRATEGY_MIN_CANDLES

# Module singleton — lives for the worker process lifetime.
worker_candle_cache: WorkerCandleCache | None = None


def get_worker_candle_cache() -> WorkerCandleCache:
    global worker_candle_cache
    if worker_candle_cache is None:
        worker_candle_cache = WorkerCandleCache()
    return worker_candle_cache


def reset_worker_candle_cache() -> None:
    """Test helper — drop in-memory candle windows."""
    global worker_candle_cache
    if worker_candle_cache is not None:
        worker_candle_cache.clear()


class WorkerCandleCache:
    """In-memory candle window per instrument/timeframe (DB remains canonical)."""

    def __init__(self) -> None:
        self._windows: dict[tuple[str, str], list[Candle]] = {}

    def clear(self) -> None:
        self._windows.clear()

    def get_window(
        self,
        store,
        instrument_id: str,
        timeframe: str,
        *,
        lookback: int,
    ) -> list[Candle]:
        """Return the strategy lookback window, warming or extending incrementally."""
        key = (instrument_id, timeframe)
        cached = self._windows.get(key)
        if cached is None:
            candles = store.list_recent_candles(instrument_id, timeframe, limit=lookback)
            self._windows[key] = list(candles)
            return list(candles)

        latest = cached[-1].timestamp if cached else None
        if latest is not None:
            tail = store.list_candles_after(
                instrument_id,
                timeframe,
                after=latest,
            )
            if tail:
                cached = _merge_trim(cached, tail, lookback)
                self._windows[key] = cached
        return list(cached)

    def candles_since(
        self,
        store,
        instrument_id: str,
        timeframe: str,
        since: datetime,
        *,
        lookback: int,
        limit: int | None = None,
    ) -> list[Candle]:
        """Slice the working set from ``since`` (PM / flatten paths)."""
        window = self.get_window(
            store,
            instrument_id,
            timeframe,
            lookback=lookback,
        )
        out = [c for c in window if c.timestamp >= since]
        if limit is not None and len(out) > limit:
            return out[-limit:]
        return out

    def replace_window(
        self,
        instrument_id: str,
        timeframe: str,
        candles: list[Candle],
        *,
        lookback: int,
    ) -> None:
        """Explicit refresh after bootstrap writes (mock warm path)."""
        self._windows[(instrument_id, timeframe)] = _merge_trim([], candles, lookback)


def _merge_trim(existing: list[Candle], new_rows: list[Candle], max_len: int) -> list[Candle]:
    by_ts: dict[datetime, Candle] = {c.timestamp: c for c in existing}
    for candle in new_rows:
        by_ts[candle.timestamp] = candle
    merged = [by_ts[ts] for ts in sorted(by_ts)]
    if len(merged) > max_len:
        merged = merged[-max_len:]
    return merged


def ensure_strategy_candles(
    store,
    instrument,
    timeframe: str,
    settings_dict: dict,
    *,
    lookback: int,
) -> list[Candle]:
    """Strategy-path candle load with optional mock bootstrap."""
    from quantara_engine.core.config import settings
    from quantara_engine.market_data.factory import get_market_data_provider

    cache = get_worker_candle_cache()
    stored = store.count_candles(instrument.id, timeframe)
    if stored < STRATEGY_MIN_CANDLES:
        if settings_dict.get("market_data_provider") == "mock" or settings.market_data_provider == "mock":
            provider = get_market_data_provider("mock")
            generated = provider.generate_candles(
                instrument.id, timeframe, STRATEGY_MIN_CANDLES + 50
            )
            for candle in generated:
                store.upsert_candle(candle)
            cache.replace_window(instrument.id, timeframe, generated, lookback=lookback)
        else:
            return []

    candles = cache.get_window(store, instrument.id, timeframe, lookback=lookback)
    if len(candles) < STRATEGY_MIN_CANDLES:
        return []
    return candles
