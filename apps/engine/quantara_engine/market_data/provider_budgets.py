"""Per-provider request budgets and health tracking."""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from enum import IntEnum
from typing import Any

from quantara_engine.persistence.store import TradingStore

logger = logging.getLogger(__name__)

SETTINGS_PREFIX = "provider_budget:"

LIMITS: dict[str, dict[str, int]] = {
    "tiingo": {"hourly": 50, "daily": 1000},
    "alpaca": {"minute": 200, "daily": 100000},
}


class FetchPriority(IntEnum):
    OPEN_POSITION = 1
    CATCH_UP = 2
    SCHEDULED = 3
    SPOT_UI = 4
    AUDIT = 5


def _today() -> str:
    return date.today().isoformat()


def _hour_key() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H")


def _settings_key(provider: str) -> str:
    return f"{SETTINGS_PREFIX}{provider}"


def _empty_state(provider: str) -> dict[str, Any]:
    return {
        "provider": provider,
        "date": _today(),
        "hour": _hour_key(),
        "used_hour": 0,
        "used_day": 0,
        "last_success": None,
        "last_error": None,
        "active_symbols": [],
        "status": "unknown",
    }


def _load(store: TradingStore | None, provider: str) -> dict[str, Any]:
    if store is None:
        return _empty_state(provider)
    raw = store.get_settings_dict().get(_settings_key(provider))
    if not isinstance(raw, dict):
        return _empty_state(provider)
    if raw.get("date") != _today():
        raw["date"] = _today()
        raw["used_day"] = 0
    if raw.get("hour") != _hour_key():
        raw["hour"] = _hour_key()
        raw["used_hour"] = 0
    return raw


def _save(store: TradingStore | None, provider: str, state: dict[str, Any]) -> None:
    if store is None:
        return
    store.update_settings(_settings_key(provider), state, description=f"{provider} request budget")


def can_request(store: TradingStore | None, provider: str, count: int = 1) -> bool:
    limits = LIMITS.get(provider, {})
    state = _load(store, provider)
    if limits.get("hourly") and state["used_hour"] + count > limits["hourly"]:
        return False
    if limits.get("minute") and state.get("used_minute", 0) + count > limits["minute"]:
        return False
    if limits.get("daily") and state["used_day"] + count > limits["daily"]:
        return False
    return True


def record_request(
    store: TradingStore | None,
    provider: str,
    *,
    symbol: str,
    caller: str,
    count: int = 1,
    success: bool = True,
    error: str | None = None,
) -> None:
    state = _load(store, provider)
    state["used_hour"] = int(state.get("used_hour", 0)) + count
    state["used_day"] = int(state.get("used_day", 0)) + count
    symbols = set(state.get("active_symbols") or [])
    symbols.add(symbol)
    state["active_symbols"] = sorted(symbols)
    now = datetime.now(timezone.utc).isoformat()
    if success:
        state["last_success"] = now
        state["status"] = "healthy"
    else:
        state["last_error"] = error or "unknown"
        state["status"] = "error"
    _save(store, provider, state)
    logger.debug(
        "provider_budget %s caller=%s symbol=%s success=%s hour=%s day=%s",
        provider,
        caller,
        symbol,
        success,
        state["used_hour"],
        state["used_day"],
    )


def status_payload(store: TradingStore | None, provider: str) -> dict[str, Any]:
    limits = LIMITS.get(provider, {})
    state = _load(store, provider)
    hourly_limit = limits.get("hourly")
    daily_limit = limits.get("daily") or limits.get("minute")
    return {
        "provider": provider,
        "status": state.get("status", "unknown"),
        "used_hour": state.get("used_hour", 0),
        "hourly_limit": hourly_limit,
        "remaining_hour": (hourly_limit - state.get("used_hour", 0)) if hourly_limit else None,
        "used_day": state.get("used_day", 0),
        "daily_limit": daily_limit,
        "remaining_day": (daily_limit - state.get("used_day", 0)) if daily_limit else None,
        "active_symbols": state.get("active_symbols") or [],
        "last_success": state.get("last_success"),
        "last_error": state.get("last_error"),
    }


def all_provider_status(store: TradingStore | None) -> dict[str, dict[str, Any]]:
    from quantara_engine.market_data.credits import status_payload as twelve_status

    worker_raw = {}
    if store is not None:
        worker_raw = store.get_settings_dict().get("worker_status:data_fetcher") or {}

    td = twelve_status(store)
    alpaca = status_payload(store, "alpaca")
    tiingo = status_payload(store, "tiingo")

    # Infer from worker payload when budget tracker has not recorded yet.
    if alpaca.get("status") == "unknown" and worker_raw.get("last_run"):
        alpaca["status"] = worker_raw.get("status", "healthy")
        alpaca["last_success"] = worker_raw.get("last_run")
    if tiingo.get("status") == "unknown" and worker_raw.get("last_run"):
        tiingo["status"] = worker_raw.get("status", "healthy")
        tiingo["last_success"] = worker_raw.get("last_run")

    errors = worker_raw.get("errors") or []
    if isinstance(errors, list) and any("429" in str(item) for item in errors):
        td["status"] = "blocked"
        td["last_error"] = next((str(item) for item in errors if "429" in str(item)), td.get("last_error"))
    elif int(td.get("used_today") or 0) >= 720:
        td["status"] = "blocked"

    return {
        "twelvedata": td,
        "alpaca": alpaca,
        "tiingo": tiingo,
    }
