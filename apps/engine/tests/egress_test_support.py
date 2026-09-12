"""Shared helpers for egress regression and integration tests."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from quantara_engine.domain.types import Candle
from quantara_engine.market_data.candle_working_set import (
    WorkerCandleCache,
    get_worker_candle_cache,
    reset_worker_candle_cache,
)
from quantara_engine.market_data.polling import STRATEGY_MIN_CANDLES
from quantara_engine.persistence.egress_metrics import EgressMetrics

CANDLE_LOOKBACK = STRATEGY_MIN_CANDLES + 50
COMPETITION_TIMEFRAMES = ("5m", "15m", "1h")
TIMEFRAME_MINUTES = {"5m": 5, "15m": 15, "1h": 60}


def _next_candle_start(latest: datetime, timeframe: str) -> datetime:
    return latest + timedelta(minutes=TIMEFRAME_MINUTES[timeframe])


class IncrementalCandleBackingStore:
    """Realistic in-memory candle DB for incremental cache proofs."""

    def __init__(self) -> None:
        self._rows: dict[tuple[str, str], list[Candle]] = defaultdict(list)
        self.egress_metrics = EgressMetrics()

    def seed(self, instrument_id: str, timeframe: str, candles: list[Candle]) -> None:
        key = (instrument_id, timeframe)
        by_ts = {c.timestamp: c for c in self._rows[key]}
        for candle in candles:
            by_ts[candle.timestamp] = candle
        self._rows[key] = [by_ts[ts] for ts in sorted(by_ts)]

    def append(self, instrument_id: str, timeframe: str, candle: Candle) -> None:
        self.seed(instrument_id, timeframe, [candle])

    def _note(self, label: str, rows: list[Candle]) -> None:
        self.egress_metrics.note_query(
            label,
            candle_rows=len(rows),
            candle_payload_source=rows,
        )

    def list_recent_candles(self, instrument_id: str, timeframe: str, limit: int = 50) -> list[Candle]:
        rows = list(self._rows[(instrument_id, timeframe)][-limit:])
        self._note("list_recent_candles", rows)
        return rows

    def list_candles_after(
        self,
        instrument_id: str,
        timeframe: str,
        after: datetime,
        *,
        limit: int | None = None,
    ) -> list[Candle]:
        rows = [c for c in self._rows[(instrument_id, timeframe)] if c.timestamp > after]
        if limit is not None:
            rows = rows[:limit]
        self._note("list_candles_after", rows)
        return rows

    def list_candles(
        self,
        instrument_id: str,
        timeframe: str,
        limit: int | None = None,
        since: datetime | None = None,
    ) -> list[Candle]:
        rows = [
            c
            for c in self._rows[(instrument_id, timeframe)]
            if since is None or c.timestamp >= since
        ]
        if limit is not None:
            rows = rows[:limit]
        self._note("list_candles", rows)
        return rows


def make_candle_series(
    count: int,
    *,
    instrument_id: str = "inst1",
    timeframe: str = "5m",
    start: datetime | None = None,
    base_price: Decimal = Decimal("100"),
    high_offset: Decimal = Decimal("0"),
) -> list[Candle]:
    start = start or datetime(2026, 9, 1, tzinfo=timezone.utc)
    out: list[Candle] = []
    for i in range(count):
        ts = start + timedelta(minutes=5 * i)
        price = base_price + Decimal(i)
        high = price + high_offset
        out.append(
            Candle(
                instrument_id=instrument_id,
                timeframe=timeframe,
                timestamp=ts,
                open=price,
                high=high,
                low=price - Decimal("1"),
                close=price,
                volume=Decimal("1"),
            )
        )
    return out


def warm_cache_for_pairs(
    store,
    pairs: list[tuple[str, str]],
    *,
    lookback: int = CANDLE_LOOKBACK,
) -> None:
    cache = get_worker_candle_cache()
    for instrument_id, timeframe in pairs:
        cache.get_window(store, instrument_id, timeframe, lookback=lookback)


def simulate_steady_state_strategy_reads(
    store,
    *,
    lookback: int = CANDLE_LOOKBACK,
) -> EgressMetrics:
    """One post-warmup strategy cycle read pattern across all competition pairs."""
    from collections import defaultdict as dd

    metrics = EgressMetrics()
    store.egress_metrics = metrics
    reset_worker_candle_cache()

    _, _, entries = store.list_all_competition_entries()
    instrument_ids = sorted({entry["instance"].instrument_id for entry in entries})
    pairs = [(iid, tf) for iid in instrument_ids for tf in COMPETITION_TIMEFRAMES]
    warm_cache_for_pairs(store, pairs, lookback=lookback)
    warm_metrics = metrics.summary()
    metrics.reset()

    for instrument_id, timeframe in pairs:
        latest = store.latest_candle_timestamp(instrument_id, timeframe)
        if latest is None:
            continue
        fresh = make_candle_series(
            1,
            instrument_id=instrument_id,
            timeframe=timeframe,
            start=_next_candle_start(latest, timeframe),
        )[0]
        store.upsert_candle(fresh)
    store.session.flush()

    groups: dict[tuple[str, str], list] = dd(list)
    for entry in entries:
        key = (entry["instance"].instrument_id, entry["instance"].timeframe)
        groups[key].append(entry)

    cache = get_worker_candle_cache()
    for (instrument_id, timeframe), group in groups.items():
        candles = cache.get_window(store, instrument_id, timeframe, lookback=lookback)
        if not candles:
            continue
        instance_ids = [entry["instance"].id for entry in group]
        store.fully_processed_candle_timestamps(
            instance_ids,
            instrument_id,
            since=candles[0].timestamp,
        )
        portfolio_ids = [entry["portfolio"].id for entry in group]
        store.batch_load_portfolio_states(portfolio_ids)

    metrics._warm_summary = warm_metrics  # type: ignore[attr-defined]
    return metrics
