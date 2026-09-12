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
HEALTH_SYNC_INTERVAL_SECONDS = 3600

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
    """Reconcile provider-reported usage from Twelve Data api_usage."""
    if store is None:
        return
    state = _load_state(store)
    daily_usage = provider_usage.get("daily_usage")
    if daily_usage is not None:
        state["provider_daily_usage"] = int(daily_usage)
    daily_limit = provider_usage.get("plan_daily_limit")
    if daily_limit is None:
        daily_limit = provider_usage.get("daily_limit")
    if daily_limit is not None:
        state["provider_daily_limit"] = int(daily_limit)
    now = datetime.now(timezone.utc).isoformat()
    state["last_sync"] = now
    state["last_health_sync"] = now
    state["health_status"] = "healthy"
    err = str(state.get("last_error") or "").lower()
    if "429" not in err and "run out of api credits" not in err:
        state.pop("last_error", None)
    _save_state(store, state)


def record_health_error(
    store: TradingStore | None,
    error: str,
    *,
    code: int | None = None,
) -> None:
    """Persist a failed Twelve Data health sync (does not affect trading state)."""
    if store is None:
        return
    state = _load_state(store)
    state["last_error"] = error
    lower = error.lower()
    if code == 429 or "429" in lower or "run out of api credits" in lower:
        state["health_status"] = "blocked"
    else:
        state["health_status"] = "error"
    state["last_health_attempt"] = datetime.now(timezone.utc).isoformat()
    _save_state(store, state)


def mark_blocked(store: TradingStore | None, error: str) -> None:
    """Persist provider-side block (e.g. HTTP 429) for fast-skip until UTC day reset."""
    if store is None:
        return
    state = _load_state(store)
    state["last_error"] = error
    state["health_status"] = "blocked"
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


def _provider_health_status(state: dict[str, Any]) -> str:
    """Health is independent from credit usage — sync success means healthy."""
    err = str(state.get("last_error") or "").lower()
    if state.get("health_status") == "blocked" or "429" in err or "run out of api credits" in err:
        return "blocked"
    if state.get("health_status") == "error":
        return "error"
    if state.get("last_health_sync") or state.get("last_sync"):
        return "healthy"
    if int(state.get("used") or 0) > 0:
        return "healthy"
    return "unknown"


def status_payload(store: TradingStore | None) -> dict[str, Any]:
    state = _load_state(store)
    if state.get("provider_daily_usage") is not None:
        used_today = int(state["provider_daily_usage"])
    else:
        used_today = int(state.get("used") or 0)
    plan_limit = int(state.get("provider_daily_limit") or DAILY_HARD_LIMIT)
    ledger_used = int(state.get("used") or 0)
    health = _provider_health_status(state)
    return {
        "provider": "twelvedata",
        "status": health,
        "date": state.get("date", _today_key()),
        "used_today": used_today,
        "remaining": max(0, plan_limit - used_today),
        "provider_plan_limit": plan_limit,
        "daily_limit": plan_limit,
        "estimated_run_rate_per_hour": estimate_run_rate_per_hour(store),
        "daily_hard_limit": DAILY_HARD_LIMIT,
        "internal_guard_limit": INTERNAL_GUARD_LIMIT,
        "guard_limit": INTERNAL_GUARD_LIMIT,
        "internal_guard_active": ledger_used >= INTERNAL_GUARD_LIMIT,
        "ledger_used": ledger_used,
        "last_sync": state.get("last_sync"),
        "last_success": state.get("last_health_sync") or state.get("last_sync"),
        "last_error": state.get("last_error"),
    }


def refresh_twelve_data_health(*, force: bool = False) -> dict[str, Any]:
    """Sync Twelve Data api_usage into settings (short-lived DB sessions)."""
    from quantara_engine.db.session import session_scope
    from quantara_engine.market_data.adapters.twelvedata import TwelveDataError, TwelveDataMarketDataProvider
    from quantara_engine.persistence.store import TradingStore

    with session_scope() as session:
        store = TradingStore(session)
        state = _load_state(store)
        last = state.get("last_health_sync")
        if not force and last:
            try:
                last_dt = datetime.fromisoformat(str(last))
                age = (datetime.now(timezone.utc) - last_dt).total_seconds()
                if age < HEALTH_SYNC_INTERVAL_SECONDS:
                    return status_payload(store)
            except ValueError:
                pass

    usage_payload: dict[str, Any] | None = None
    error: tuple[str, int | None] | None = None
    try:
        provider = TwelveDataMarketDataProvider(
            store=None,
            caller="twelve_data_health_sync",
            priority=FetchPriority.AUDIT,
        )
        usage_payload = provider.fetch_api_usage()
    except TwelveDataError as exc:
        error = (str(exc), getattr(exc, "code", None))
        logger.warning("Twelve Data health sync failed: %s", exc)
    except Exception as exc:
        error = (str(exc), None)
        logger.warning("Twelve Data health sync unexpected error: %s", exc)

    with session_scope() as session:
        store = TradingStore(session)
        if usage_payload is not None:
            sync_provider_usage(store, usage_payload)
        elif error is not None:
            record_health_error(store, error[0], code=error[1])
        return status_payload(store)


def maybe_refresh_twelve_data_health(*, force: bool = False) -> None:
    """Best-effort health refresh — never raises."""
    try:
        refresh_twelve_data_health(force=force)
    except Exception as exc:
        logger.warning("Twelve Data health refresh skipped: %s", exc)
