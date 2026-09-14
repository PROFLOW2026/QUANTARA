"""Persist canonical 5m (+15m/1h) derived from stored completed 1m bars."""

from __future__ import annotations

import logging
from datetime import datetime

from quantara_engine.market_data.aggregation import (
    incremental_1m_source_limit,
    incremental_derive_from_1m,
    incremental_derive_from_5m,
    incremental_source_limit,
)
from quantara_engine.market_data.polling import FAST_PROTECTION_TIMEFRAME, PROVIDER_TIMEFRAME
from quantara_engine.market_data.validation import validate_candle
from quantara_engine.persistence.store import TradingStore

logger = logging.getLogger(__name__)


def derive_higher_from_1m(
    store: TradingStore,
    instrument_id: str,
    new_1m_timestamps: list[datetime] | None = None,
    *,
    session_mode: str = "utc",
) -> tuple[int, int]:
    """
    Aggregate completed 1m → canonical 5m, then 5m → 15m/1h.

    When new_1m_timestamps is None, uses a short recent 1m window (catch-up).
    Returns (upserted_5m_count, upserted_higher_count).
    """
    if new_1m_timestamps:
        lookback = incremental_1m_source_limit(len(new_1m_timestamps))
        touch = list(new_1m_timestamps)
    else:
        lookback = incremental_1m_source_limit(5)
        touch = None

    base_1m = store.list_recent_candles(instrument_id, FAST_PROTECTION_TIMEFRAME, limit=lookback)
    if not base_1m:
        return 0, 0

    if touch is None:
        touch = [c.timestamp for c in base_1m]

    derived_5m = incremental_derive_from_1m(
        base_1m,
        touch,
        session_mode=session_mode,
    )
    count_5m = 0
    new_5m_ts: list[datetime] = []
    for candle in derived_5m:
        try:
            validate_candle(candle)
        except Exception as exc:
            logger.warning("Invalid 1m→5m candle skipped: %s", exc)
            continue
        store.upsert_candle(candle)
        count_5m += 1
        new_5m_ts.append(candle.timestamp)

    if not new_5m_ts:
        return 0, 0

    higher_lookback = incremental_source_limit(len(new_5m_ts))
    base_5m = store.list_recent_candles(instrument_id, PROVIDER_TIMEFRAME, limit=higher_lookback)
    higher = 0
    for candle in incremental_derive_from_5m(
        base_5m,
        new_5m_ts,
        session_mode=session_mode,
    ):
        try:
            validate_candle(candle)
        except Exception as exc:
            logger.warning("Invalid derived candle skipped after 1m→5m: %s", exc)
            continue
        store.upsert_candle(candle)
        higher += 1
    return count_5m, higher
