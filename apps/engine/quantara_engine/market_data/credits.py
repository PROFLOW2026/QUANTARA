"""Twelve Data credit accounting and conservative daily guard."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timezone
from enum import IntEnum
from typing import Any

from quantara_engine.persistence.store import TradingStore

logger = logging.getLogger(__name__)

SETTINGS_KEY = "provider_credits:twelvedata"
DAILY_HARD_LIMIT = 800
INTERNAL_GUARD_LIMIT = 720
MAX_LOG_ENTRIES = 500

ENDPOINT_CREDITS: dict[str, int] = {
    "time_series": 1,
    "quote": 1,
    "price": 1,
    "api_usage": 0,
    "symbol_search": 1,
}


class FetchPriority(IntEnum):
    OPEN_POSITION = 1
    CATCH_UP = 2
    SCHEDULED = 3
    SPOT_UI = 4
    AUDIT = 5


@dataclass(frozen=True)
class CreditEvent:
    provider: str
    endpoint: str
    symbol: str
    interval: str | None
    caller: str
    credits: int
    timestamp: str


def _today_key() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _empty_state() -> dict[str, Any]:
    return {
        "date": _today_key(),
        "used": 0,
        "events": [],
        "last_sync": None,
        "provider_daily_limit": DAILY_HARD_LIMIT,
    }


def _load_state(store: TradingStore | None) -> dict[str, Any]:
    if store is None:
        return _empty_state()
    raw = store.get_settings_dict().get(SETTINGS_KEY)
    if not isinstance(raw, dict):
        return _empty_state()
    if raw.get("date") != _today_key():
        return _empty_state()
    events = raw.get("events")
    if not isinstance(events, list):
        raw["events"] = []
    return raw


def _save_state(store: TradingStore | None, state: dict[str, Any]) -> None:
    if store is None:
        return
    events = state.get("events")
    if isinstance(events, list) and len(events) > MAX_LOG_ENTRIES:
        state["events"] = events[-MAX_LOG_ENTRIES:]
    store.update_settings(SETTINGS_KEY, state)


def credits_for_endpoint(endpoint: str) -> int:
    return ENDPOINT_CREDITS.get(endpoint, 1)


def record_usage(
    store: TradingStore | None,
    *,
    endpoint: str,
    symbol: str,
    interval: str | None,
    caller: str,
    credits: int | None = None,
) -> CreditEvent:
    """Append a credit event to today's ledger."""
    consumed = credits if credits is not None else credits_for_endpoint(endpoint)
    event = CreditEvent(
        provider="twelvedata",
        endpoint=endpoint,
        symbol=symbol,
        interval=interval,
        caller=caller,
        credits=consumed,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )
    state = _load_state(store)
    state["used"] = int(state.get("used") or 0) + consumed
    events = state.setdefault("events", [])
    if isinstance(events, list):
        events.append(event.__dict__)
    _save_state(store, state)
    logger.info(
        "Twelve Data credit +%s endpoint=%s caller=%s used_today=%s",
        consumed,
        endpoint,
        caller,
        state["used"],
    )
    return event


def sync_provider_usage(store: TradingStore | None, provider_usage: dict[str, Any]) -> None:
    """Optional reconcile from Twelve Data api_usage."""
    if store is None:
        return
    state = _load_state(store)
    daily_usage = provider_usage.get("daily_usage")
    if daily_usage is not None:
        state["provider_daily_usage"] = int(daily_usage)
    daily_limit = provider_usage.get("daily_limit")
    if daily_limit is not None:
        state["provider_daily_limit"] = int(daily_limit)
    state["last_sync"] = datetime.now(timezone.utc).isoformat()
    _save_state(store, state)


def mark_blocked(store: TradingStore | None, error: str) -> None:
    """Persist provider-side block (e.g. HTTP 429) for fast-skip until UTC day reset."""
    if store is None:
        return
    state = _load_state(store)
    state["last_error"] = error
    state["used"] = max(int(state.get("used") or 0), DAILY_HARD_LIMIT)
    _save_state(store, state)


def is_blocked(store: TradingStore | None) -> bool:
    """True when Twelve Data daily credits are exhausted (429 / guard)."""
    payload = status_payload(store)
    if payload.get("status") == "blocked":
        return True
    err = str(payload.get("last_error") or "").lower()
    return "429" in err or "run out of api credits" in err


def can_fetch(store: TradingStore | None, priority: FetchPriority) -> bool:
    """Conservative guard — critical fetches always allowed until hard cap."""
    state = _load_state(store)
    used = int(state.get("provider_daily_usage") or state.get("used") or 0)
    if used >= DAILY_HARD_LIMIT:
        return priority <= FetchPriority.OPEN_POSITION
    if used >= INTERNAL_GUARD_LIMIT and priority >= FetchPriority.SPOT_UI:
        return False
    if used >= INTERNAL_GUARD_LIMIT - 20 and priority >= FetchPriority.AUDIT:
        return False
    return True


def estimate_run_rate_per_hour(store: TradingStore | None) -> float:
    state = _load_state(store)
    events = state.get("events")
    if not isinstance(events, list) or len(events) < 2:
        return 0.0
    first = datetime.fromisoformat(str(events[0]["timestamp"]))
    last = datetime.fromisoformat(str(events[-1]["timestamp"]))
    hours = max((last - first).total_seconds() / 3600, 1 / 60)
    return round(int(state.get("used") or 0) / hours, 2)


def status_payload(store: TradingStore | None) -> dict[str, Any]:
    state = _load_state(store)
    used = int(state.get("provider_daily_usage") or state.get("used") or 0)
    limit = int(state.get("provider_daily_limit") or DAILY_HARD_LIMIT)
    remaining = max(0, limit - used)
    if used >= DAILY_HARD_LIMIT:
        status = "blocked"
    elif used >= INTERNAL_GUARD_LIMIT:
        status = "stale"
    elif used > 0:
        status = "healthy"
    else:
        status = "unknown"
    return {
        "provider": "twelvedata",
        "status": status,
        "date": state.get("date", _today_key()),
        "used_today": used,
        "remaining": remaining,
        "estimated_run_rate_per_hour": estimate_run_rate_per_hour(store),
        "daily_hard_limit": DAILY_HARD_LIMIT,
        "internal_guard_limit": INTERNAL_GUARD_LIMIT,
        "guard_limit": INTERNAL_GUARD_LIMIT,
        "ledger_used": int(state.get("used") or 0),
        "last_sync": state.get("last_sync"),
        "last_error": state.get("last_error"),
    }
