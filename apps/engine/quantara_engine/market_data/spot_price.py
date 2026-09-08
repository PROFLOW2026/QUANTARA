"""Spot XAU/USD display price — derived from persisted candles, not live quotes."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from quantara_engine.persistence.store import TradingStore

WORKER_STATUS_KEY = "worker_status:data_fetcher"
STALE_SPOT_MINUTES = 10


def build_spot_snapshot(
    price: Decimal | float,
    *,
    fetched_at: datetime | None = None,
    previous_close: Decimal | float | None = None,
    source: str = "candle_5m_close",
) -> dict[str, Any]:
    fetched_at = fetched_at or datetime.now(timezone.utc)
    price_f = float(price)
    change = 0.0
    change_pct = 0.0
    if previous_close is not None:
        prev = float(previous_close)
        change = price_f - prev
        change_pct = (change / prev * 100) if prev else 0.0

    return {
        "price": price_f,
        "change": change,
        "change_pct": change_pct,
        "updated_at": fetched_at.isoformat(),
        "fetched_at": fetched_at.isoformat(),
        "source": source,
    }


def read_spot_snapshot(store: TradingStore) -> dict[str, Any] | None:
    settings_dict = store.get_settings_dict()
    raw = settings_dict.get(WORKER_STATUS_KEY)
    if not isinstance(raw, dict):
        return None
    spot = raw.get("spot")
    return spot if isinstance(spot, dict) and spot.get("price") is not None else None


def spot_age_minutes(spot: dict[str, Any], now: datetime | None = None) -> float:
    now = now or datetime.now(timezone.utc)
    ts_raw = spot.get("fetched_at") or spot.get("updated_at")
    if not ts_raw:
        return float("inf")
    try:
        ts = datetime.fromisoformat(str(ts_raw))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
    except ValueError:
        return float("inf")
    return max(0.0, (now - ts).total_seconds() / 60)


def is_spot_stale(spot: dict[str, Any], now: datetime | None = None) -> bool:
    return spot_age_minutes(spot, now) > STALE_SPOT_MINUTES


def save_spot_snapshot(store: TradingStore, spot: dict[str, Any]) -> None:
    settings_dict = store.get_settings_dict()
    worker_status = settings_dict.get(WORKER_STATUS_KEY)
    if not isinstance(worker_status, dict):
        worker_status = {}
    worker_status = {**worker_status, "spot": spot}
    store.update_settings(WORKER_STATUS_KEY, worker_status)


def update_spot_from_latest_5m(store: TradingStore, instrument_id: str) -> dict[str, Any] | None:
    """Refresh cached spot from the latest completed 5m candle close (no provider call)."""
    rows = store.list_recent_candles(instrument_id, "5m", limit=2)
    if not rows:
        return read_spot_snapshot(store)
    latest = rows[-1]
    previous = rows[-2].close if len(rows) > 1 else None
    spot = build_spot_snapshot(
        latest.close,
        fetched_at=latest.timestamp,
        previous_close=previous,
        source="candle_5m_close",
    )
    save_spot_snapshot(store, spot)
    return spot


def resolve_spot_snapshot(
    store: TradingStore,
    *,
    allow_fetch: bool = False,
) -> dict[str, Any] | None:
    """Return cached spot only — UI/API routes must not trigger provider calls."""
    spot = read_spot_snapshot(store)
    if spot and not is_spot_stale(spot):
        return spot
    if allow_fetch:
        return spot
    return spot


def spot_response_fields(spot: dict[str, Any]) -> dict[str, Any]:
    age = spot_age_minutes(spot)
    return {
        "price": spot["price"],
        "change": spot.get("change", 0),
        "change_pct": spot.get("change_pct", 0),
        "last_update": spot.get("updated_at") or spot.get("fetched_at"),
        "timeframe": "spot",
        "price_source": spot.get("source", "candle_5m_close"),
        "data_age_minutes": round(age, 1),
        "is_stale": is_spot_stale(spot),
    }
