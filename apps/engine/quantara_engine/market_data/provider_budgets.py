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
    "coinbase": {"hourly": 300, "daily": 5000},
}

TIINGO_HOURLY_HARD_LIMIT = int(LIMITS["tiingo"]["hourly"])
TIINGO_USABLE_CANDLE_BUDGET = 42
TIINGO_FX_RESERVE = 8
TIINGO_SAFETY_HEADROOM = TIINGO_HOURLY_HARD_LIMIT - TIINGO_USABLE_CANDLE_BUDGET


class FetchPriority(IntEnum):
    OPEN_POSITION = 1
    CATCH_UP = 2
    SCHEDULED = 3
    SPOT_UI = 4
    AUDIT = 5


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


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


def tiingo_used_hour(store: TradingStore | None) -> int:
    return int(_load(store, "tiingo").get("used_hour", 0))


def tiingo_hard_remaining(store: TradingStore | None) -> int:
    return max(0, TIINGO_HOURLY_HARD_LIMIT - tiingo_used_hour(store))


def tiingo_candle_remaining(store: TradingStore | None) -> int:
    return max(0, min(TIINGO_USABLE_CANDLE_BUDGET - tiingo_used_hour(store), tiingo_hard_remaining(store)))


def tiingo_fx_remaining(store: TradingStore | None) -> int:
    return tiingo_hard_remaining(store)


def tiingo_budget_mode(store: TradingStore | None) -> str:
    used = tiingo_used_hour(store)
    if used >= TIINGO_HOURLY_HARD_LIMIT:
        return "exhausted"
    if used >= TIINGO_USABLE_CANDLE_BUDGET - TIINGO_FX_RESERVE:
        return "conservation"
    return "healthy"


def tiingo_budget_snapshot(store: TradingStore | None) -> dict[str, Any]:
    used = tiingo_used_hour(store)
    hard_remaining = tiingo_hard_remaining(store)
    candle_remaining = tiingo_candle_remaining(store)
    return {
        "used_hour": used,
        "hourly_hard_limit": TIINGO_HOURLY_HARD_LIMIT,
        "usable_candle_budget": TIINGO_USABLE_CANDLE_BUDGET,
        "fx_reserve": TIINGO_FX_RESERVE,
        "hard_remaining": hard_remaining,
        "candle_remaining": candle_remaining,
        "fx_remaining": tiingo_fx_remaining(store),
        "mode": tiingo_budget_mode(store),
    }


def can_request_tiingo_candle(store: TradingStore | None, count: int = 1) -> bool:
    if store is None:
        return True
    return tiingo_candle_remaining(store) >= count and tiingo_hard_remaining(store) >= count


def can_request_tiingo_fx(store: TradingStore | None, count: int = 1) -> bool:
    if store is None:
        return True
    return tiingo_fx_remaining(store) >= count


def can_request(
    store: TradingStore | None,
    provider: str,
    count: int = 1,
    *,
    purpose: str = "candles",
) -> bool:
    if provider == "tiingo":
        if purpose == "fx_rate":
            return can_request_tiingo_fx(store, count)
        return can_request_tiingo_candle(store, count)

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
    from quantara_engine.market_data.provider_cooldown import cooldown_payload, is_in_cooldown

    limits = LIMITS.get(provider, {})
    state = _load(store, provider)
    if store and is_in_cooldown(store, provider):
        cd = cooldown_payload(store, provider) or {}
        return {
            "provider": provider,
            "status": "cooldown",
            "used_hour": state.get("used_hour", 0),
            "hourly_limit": limits.get("hourly"),
            "used_day": state.get("used_day", 0),
            "daily_limit": limits.get("daily") or limits.get("minute"),
            "active_symbols": state.get("active_symbols") or [],
            "last_success": state.get("last_success"),
            "last_error": cd.get("reason") or state.get("last_error"),
            "cooldown_until": cd.get("until"),
        }
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
    tiingo_snap = tiingo_budget_snapshot(store)
    tiingo.update(
        {
            "usable_budget": tiingo_snap["usable_candle_budget"],
            "candle_remaining": tiingo_snap["candle_remaining"],
            "fx_reserve": tiingo_snap["fx_reserve"],
            "budget_mode": tiingo_snap["mode"],
            "fallback_mode": tiingo_snap["mode"] != "healthy",
        }
    )
    if tiingo_snap["mode"] == "exhausted":
        tiingo["status"] = "exhausted"
    elif tiingo_snap["mode"] == "conservation" and tiingo.get("status") == "healthy":
        tiingo["status"] = "conservation"

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

    return {
        "twelvedata": td,
        "alpaca": alpaca,
        "tiingo": tiingo,
    }
