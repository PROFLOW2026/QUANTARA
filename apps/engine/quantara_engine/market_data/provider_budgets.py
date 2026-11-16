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
    "finnhub": {"minute": 50, "hourly": 150, "daily": 3000},
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


def record_provider_error(
    store: TradingStore | None,
    provider: str,
    error: str,
    *,
    symbol: str = "",
    caller: str = "provider_error",
) -> None:
    """Record a failed provider request and optional cooldown (Coinbase adapter path)."""
    from quantara_engine.market_data.provider_cooldown import mark_cooldown

    record_request(
        store,
        provider,
        symbol=symbol or provider,
        caller=caller,
        count=1,
        success=False,
        error=error,
    )
    if store is not None and provider == "coinbase":
        mark_cooldown(store, provider, reason=error[:500])


def _apply_request_record(
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
        state["last_error"] = None
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


def _orm_session(store: TradingStore | None):
    """Real SQLAlchemy session only — test doubles stay on the in-place path."""
    from sqlalchemy.orm import Session

    session = getattr(store, "session", None)
    return session if isinstance(session, Session) else None


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
    """Record provider usage without pinning the budget row to a long ingest transaction.

    Candle fetches call this while their own DB transaction is still open. Writing the
    shared ``provider_budget:*`` row on that same transaction deadlocks the moment a
    second session (or the same thread's nested session) updates the row. Budget
    accounting therefore commits on a short autonomous transaction.
    """
    kwargs = {
        "symbol": symbol,
        "caller": caller,
        "count": count,
        "success": success,
        "error": error,
    }
    if store is None or _orm_session(store) is None:
        _apply_request_record(store, provider, **kwargs)
        return

    from sqlalchemy import text
    from sqlalchemy.exc import OperationalError

    from quantara_engine.db.session import session_scope

    try:
        with session_scope() as fresh:
            fresh.execute(text("SET LOCAL lock_timeout = '2000ms'"))
            _apply_request_record(TradingStore(fresh), provider, **kwargs)
    except OperationalError as exc:
        logger.warning(
            "provider budget record skipped (%s/%s): %s",
            provider,
            symbol,
            exc,
        )
        return
    if hasattr(store, "invalidate_settings_cache"):
        store.invalidate_settings_cache()


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


def provider_candle_freshness(
    latest: dict[tuple[str, str], datetime],
    now: datetime,
) -> dict[str, dict[str, Any]]:
    """Whether each provider's open-session symbols have a fresh canonical candle.

    API reachability is not enough: a provider is fresh only when the candles
    strategies actually read are inside the staleness limit.
    """
    from quantara_engine.execution.crypto_mark_valuation import FAST_ALL_1M_SYMBOLS
    from quantara_engine.market_data.polling import is_market_data_fresh
    from quantara_engine.market_data.registry import AssetClass, ProviderName, list_target_assets
    from quantara_engine.market_data.sessions import is_forex_session, is_us_equity_rth

    def _ts(symbol: str, timeframe: str) -> datetime | None:
        ts = latest.get((symbol, timeframe))
        if ts is None:
            return None
        if ts.tzinfo is None:
            return ts.replace(tzinfo=timezone.utc)
        return ts.astimezone(timezone.utc)

    def _session_open(asset) -> bool:
        if asset.asset_class == AssetClass.CRYPTO:
            return True
        if asset.asset_class in (AssetClass.STOCK, AssetClass.INDEX):
            return is_us_equity_rth(now)
        return is_forex_session(now)

    def _symbol_fresh(symbol: str) -> tuple[bool, float | None]:
        timeframe = "1m" if symbol in FAST_ALL_1M_SYMBOLS else "5m"
        ts = _ts(symbol, timeframe)
        if ts is None and timeframe == "1m":
            ts = _ts(symbol, "5m")
            timeframe = "5m"
        if ts is None:
            return False, None
        age = round((now - ts).total_seconds() / 60, 1)
        return is_market_data_fresh(ts, timeframe, now), age

    groups: dict[str, list[tuple[str, bool]]] = {
        "alpaca": [],
        "tiingo": [],
        "coinbase": [],
        "twelvedata": [],
        "finnhub": [],
    }
    for asset in list_target_assets():
        open_now = _session_open(asset)
        if (
            asset.primary_provider == ProviderName.ALPACA
            and asset.asset_class in (AssetClass.STOCK, AssetClass.INDEX)
        ):
            groups["alpaca"].append((asset.db_symbol, open_now))
            groups["finnhub"].append((asset.db_symbol, open_now))
        elif asset.primary_provider == ProviderName.TIINGO:
            groups["tiingo"].append((asset.db_symbol, open_now))
            if asset.secondary_provider == ProviderName.TWELVE_DATA:
                groups["twelvedata"].append((asset.db_symbol, open_now))
        elif asset.primary_provider == ProviderName.COINBASE:
            groups["coinbase"].append((asset.db_symbol, open_now))

    out: dict[str, dict[str, Any]] = {}
    for name, rows in groups.items():
        open_rows = [symbol for symbol, open_now in rows if open_now]
        if not open_rows:
            out[name] = {
                "session_open": False,
                "fresh": True,
                "stale_symbols": [],
                "max_age_minutes": None,
            }
            continue
        stale: list[str] = []
        max_age: float | None = None
        for symbol in open_rows:
            fresh, age = _symbol_fresh(symbol)
            if age is not None:
                max_age = age if max_age is None else max(max_age, age)
            if not fresh:
                stale.append(symbol)
        out[name] = {
            "session_open": True,
            "fresh": not stale,
            "stale_symbols": stale,
            "max_age_minutes": max_age,
        }
    return out


def _apply_provider_candle_freshness(
    store: TradingStore | None,
    result: dict[str, dict[str, Any]],
) -> None:
    """Downgrade HEALTHY when open-session candles are older than the trading limit."""
    session = _orm_session(store)
    if session is None:
        return
    from sqlalchemy import text

    try:
        rows = session.execute(
            text(
                """
                SELECT i.symbol, c.timeframe::text AS tf, max(c.timestamp) AS ts
                FROM candles c
                JOIN instruments i ON i.id = c.instrument_id
                WHERE c.timeframe::text IN ('1m', '5m')
                GROUP BY i.symbol, c.timeframe
                """
            )
        ).all()
    except Exception:
        logger.warning("provider candle freshness query failed", exc_info=True)
        return

    latest: dict[tuple[str, str], datetime] = {}
    for symbol, timeframe, ts in rows:
        if ts is not None:
            latest[(str(symbol), str(timeframe))] = ts
    verdicts = provider_candle_freshness(latest, datetime.now(timezone.utc))
    for name, payload in result.items():
        info = verdicts.get(name)
        if not info or not info["session_open"]:
            payload["data_fresh"] = None
            continue
        payload["data_fresh"] = info["fresh"]
        payload["stale_symbols"] = info["stale_symbols"]
        payload["candle_age_minutes"] = info["max_age_minutes"]
        payload["status_basis"] = "candle_age"
        status = str(payload.get("status") or "unknown")
        if not info["fresh"] and status not in {"blocked", "exhausted", "cooldown"}:
            payload["status"] = "stale"
        elif info["fresh"] and status in {"unknown", ""}:
            payload["status"] = "healthy"


def all_provider_status(store: TradingStore | None) -> dict[str, dict[str, Any]]:
    from quantara_engine.market_data.credits import status_payload as twelve_status
    from quantara_engine.execution.fx_protection_sources import protection_sources_snapshot

    worker_raw = {}
    if store is not None:
        worker_raw = store.get_settings_dict().get("worker_status:data_fetcher") or {}

    td = twelve_status(store)
    if store is not None:
        fx_prot = protection_sources_snapshot(store)
        if fx_prot:
            td["fx_protection_sources"] = {
                sym: (entry.get("source") if isinstance(entry, dict) else entry)
                for sym, entry in fx_prot.items()
            }
            td["fx_protection_detail"] = {
                sym: {
                    "source": entry.get("source") if isinstance(entry, dict) else entry,
                    "reason": entry.get("reason") if isinstance(entry, dict) else None,
                    "last_tiingo_success_at": (
                        entry.get("last_tiingo_success_at") if isinstance(entry, dict) else None
                    ),
                    "last_td_attempt_at": (
                        entry.get("last_td_attempt_at") if isinstance(entry, dict) else None
                    ),
                }
                for sym, entry in fx_prot.items()
            }
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

    if store is not None and tiingo.get("status") in ("unknown", None, ""):
        detail = td.get("fx_protection_detail") or {}
        best: datetime | None = None
        for entry in detail.values():
            if not isinstance(entry, dict):
                continue
            raw = entry.get("last_tiingo_success_at")
            if not raw:
                continue
            try:
                dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
            except ValueError:
                continue
            if best is None or dt > best:
                best = dt
        if best is not None:
            age_h = (datetime.now(timezone.utc) - best).total_seconds() / 3600
            if age_h <= 6:
                tiingo["status"] = "healthy"
                tiingo["last_success"] = best.isoformat()
                tiingo["inferred_from"] = "fx_protection"

    # Infer from worker payload when budget tracker has not recorded yet.
    if alpaca.get("status") == "unknown" and worker_raw.get("last_run"):
        alpaca["status"] = worker_raw.get("status", "healthy")
        alpaca["last_success"] = worker_raw.get("last_run")
    if tiingo.get("status") == "unknown" and worker_raw.get("last_run"):
        tiingo["status"] = worker_raw.get("status", "healthy")
        tiingo["last_success"] = worker_raw.get("last_run")

    errors = worker_raw.get("errors") or []
    if isinstance(errors, list) and any("429" in str(item) for item in errors):
        from quantara_engine.market_data.credits import INTERNAL_GUARD_LIMIT

        used_td = int(td.get("used_today") or 0)
        if used_td >= INTERNAL_GUARD_LIMIT or td.get("quota_mode") == "HARD_GUARD":
            td["status"] = "blocked"
            td["last_error"] = next(
                (str(item) for item in errors if "429" in str(item)),
                td.get("last_error"),
            )

    from quantara_engine.market_data.adapters.finnhub import finnhub_configured, quota_snapshot

    result = {
        "twelvedata": td,
        "alpaca": alpaca,
        "tiingo": tiingo,
    }
    if finnhub_configured():
        finnhub = status_payload(store, "finnhub")
        finnhub.update(
            {
                "enabled": True,
                "role": "validation_backup",
                "role_he": "גיבוי ואימות",
                "quota": quota_snapshot(),
            }
        )
        result["finnhub"] = finnhub
    result["coinbase"] = status_payload(store, "coinbase")
    result["coinbase"].update({"role": "crypto_primary", "role_he": "קריפטו"})
    _apply_provider_candle_freshness(store, result)
    return result
