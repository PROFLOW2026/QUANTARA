"""US equity RTH session-open feed vs strategy timeframe readiness."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from quantara_engine.execution.crypto_mark_valuation import FAST_EQUITY_DB_SYMBOLS, is_fast_protection_equity
from quantara_engine.market_data.polling import (
    FAST_PROTECTION_TIMEFRAME,
    PROVIDER_TIMEFRAME,
    bar_staleness_minutes,
    is_bar_complete,
    is_market_data_fresh,
    max_staleness_minutes,
    timeframe_minutes,
)
from quantara_engine.market_data.registry import AssetDefinition
from quantara_engine.market_data.sessions import (
    US_EASTERN,
    US_RTH_OPEN,
    is_us_equity_rth,
    session_allows_entries,
    us_rth_bucket_start,
)

FEED_STATUS_HEALTHY = "healthy"
FEED_STATUS_RTH_WARMUP = "rth_warmup"
FEED_STATUS_DATA_ERROR = "data_error"

STRATEGY_STATUS_WARMUP = "session_warmup"


def _as_utc(ts: datetime) -> datetime:
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


def today_us_rth_open(now: datetime) -> datetime | None:
    """Today's regular US equity session open (09:30 ET) in UTC."""
    now = _as_utc(now)
    local = now.astimezone(US_EASTERN)
    if local.weekday() >= 5:
        return None
    open_local = datetime.combine(local.date(), US_RTH_OPEN, tzinfo=US_EASTERN)
    return open_local.astimezone(timezone.utc)


def first_rth_bucket_close(now: datetime, timeframe: str = PROVIDER_TIMEFRAME) -> datetime | None:
    """When the first RTH bucket for `timeframe` completes on this session day."""
    open_utc = today_us_rth_open(now)
    if open_utc is None:
        return None
    return open_utc + timedelta(minutes=timeframe_minutes(timeframe))


def latest_due_rth_bucket_open(now: datetime, timeframe: str = PROVIDER_TIMEFRAME) -> datetime | None:
    """
    Open time of the latest RTH bucket that should be fully closed by `now`.

    Returns None before the first bucket closes (warmup window).
    """
    if not is_us_equity_rth(now):
        return None
    first_close = first_rth_bucket_close(now, timeframe)
    if first_close is None or now < first_close:
        return None
    bucket = us_rth_bucket_start(now, timeframe)
    if bucket is None:
        return None
    # Step back one bucket — us_rth_bucket_start floors to current incomplete bucket.
    step = timedelta(minutes=timeframe_minutes(timeframe))
    if not is_bar_complete(bucket, timeframe, now):
        bucket = bucket - step
    if bucket < today_us_rth_open(now):  # type: ignore[operator]
        return None
    return bucket


def in_rth_warmup_phase(now: datetime, timeframe: str = PROVIDER_TIMEFRAME) -> bool:
    """True during RTH before the first completed bucket for `timeframe` exists."""
    if not is_us_equity_rth(now):
        return False
    first_close = first_rth_bucket_close(now, timeframe)
    return first_close is not None and now < first_close


def is_todays_rth_candle(
    candle_ts: datetime | None,
    now: datetime,
    *,
    timeframe: str = PROVIDER_TIMEFRAME,
) -> bool:
    """True when `candle_ts` is an RTH-aligned bucket on today's US session date."""
    if candle_ts is None:
        return False
    candle_ts = _as_utc(candle_ts)
    open_utc = today_us_rth_open(now)
    if open_utc is None or candle_ts < open_utc:
        return False
    bucket = us_rth_bucket_start(candle_ts, timeframe)
    return bucket is not None and bucket == candle_ts


def is_equity_1m_feed_fresh(last_1m: datetime | None, now: datetime) -> bool:
    if last_1m is None:
        return False
    return is_market_data_fresh(_as_utc(last_1m), FAST_PROTECTION_TIMEFRAME, now)


def is_equity_canonical_5m_current(
    last_5m: datetime | None,
    now: datetime,
    *,
    timeframe: str = PROVIDER_TIMEFRAME,
) -> bool:
    """
    True when stored 5m is today's RTH canonical bar and bar-close fresh.

    Pre-RTH / prior-session 5m never satisfies this during RTH.
    """
    if last_5m is None:
        return False
    last_5m = _as_utc(last_5m)
    if not is_todays_rth_candle(last_5m, now, timeframe=timeframe):
        return False
    return is_market_data_fresh(last_5m, timeframe, now)


def classify_equity_feed_health(
    *,
    last_1m: datetime | None,
    last_5m: datetime | None,
    now: datetime,
    timeframe: str = PROVIDER_TIMEFRAME,
) -> dict[str, Any]:
    """
    Live feed health (1m protection path + eventual 5m), separate from strategy readiness.

    Returns feed_status, ui_data_error, dashboard_status, stale (legacy bool).
    """
    now = _as_utc(now)
    session_open = is_us_equity_rth(now)
    one_m_fresh = is_equity_1m_feed_fresh(last_1m, now)

    if not session_open:
        return {
            "feed_status": FEED_STATUS_HEALTHY,
            "ui_data_error": False,
            "dashboard_status": "deferred",
            "stale": False,
            "session_open": False,
            "one_m_fresh": one_m_fresh,
            "in_warmup": False,
        }

    if not one_m_fresh:
        return {
            "feed_status": FEED_STATUS_DATA_ERROR,
            "ui_data_error": True,
            "dashboard_status": "stale",
            "stale": True,
            "session_open": True,
            "one_m_fresh": False,
            "in_warmup": False,
        }

    if in_rth_warmup_phase(now, timeframe):
        return {
            "feed_status": FEED_STATUS_RTH_WARMUP,
            "ui_data_error": False,
            "dashboard_status": FEED_STATUS_RTH_WARMUP,
            "stale": False,
            "session_open": True,
            "one_m_fresh": True,
            "in_warmup": True,
        }

    if is_equity_canonical_5m_current(last_5m, now, timeframe=timeframe):
        return {
            "feed_status": FEED_STATUS_HEALTHY,
            "ui_data_error": False,
            "dashboard_status": "healthy",
            "stale": False,
            "session_open": True,
            "one_m_fresh": True,
            "in_warmup": False,
        }

    return {
        "feed_status": FEED_STATUS_DATA_ERROR,
        "ui_data_error": True,
        "dashboard_status": "stale",
        "stale": True,
        "session_open": True,
        "one_m_fresh": True,
        "in_warmup": False,
    }


def classify_equity_strategy_timeframe_health(
    asset: AssetDefinition,
    last_candle_ts: datetime | None,
    now: datetime,
    *,
    timeframe: str = PROVIDER_TIMEFRAME,
    last_1m: datetime | None = None,
) -> dict[str, Any]:
    """Strategy timeframe readiness — never treats pre-RTH 5m as eligible during RTH."""
    now = _as_utc(now)
    session_open = session_allows_entries(asset.trading_sessions, now)

    base: dict[str, Any] = {
        "session_open": session_open,
        "freshness_limit_minutes": round(max_staleness_minutes(timeframe), 1),
        "last_candle_at": _as_utc(last_candle_ts).isoformat() if last_candle_ts else None,
        "in_warmup": False,
    }

    if not session_open:
        wall_age = (
            round((now - _as_utc(last_candle_ts)).total_seconds() / 60, 1)
            if last_candle_ts
            else None
        )
        return {
            **base,
            "classification": "session_closed",
            "age_minutes": wall_age,
            "staleness_since_close_minutes": (
                round(bar_staleness_minutes(_as_utc(last_candle_ts), timeframe, now), 1)
                if last_candle_ts
                else None
            ),
        }

    if not is_fast_protection_equity(asset.db_symbol):
        from quantara_engine.market_data.strategy_freshness_health import (
            classify_strategy_candle_health,
        )

        return classify_strategy_candle_health(asset, last_candle_ts, now, timeframe=timeframe)

    if in_rth_warmup_phase(now, timeframe):
        return {
            **base,
            "classification": "rth_warmup",
            "age_minutes": (
                round((now - _as_utc(last_candle_ts)).total_seconds() / 60, 1)
                if last_candle_ts
                else None
            ),
            "staleness_since_close_minutes": None,
            "in_warmup": True,
            "one_m_fresh": is_equity_1m_feed_fresh(last_1m, now),
        }

    if last_candle_ts is None:
        return {
            **base,
            "classification": "missing",
            "age_minutes": None,
            "staleness_since_close_minutes": None,
        }

    last_candle_ts = _as_utc(last_candle_ts)
    wall_age = round((now - last_candle_ts).total_seconds() / 60, 1)
    stale_since_close = round(bar_staleness_minutes(last_candle_ts, timeframe, now), 1)
    limit = round(max_staleness_minutes(timeframe), 1)

    if is_equity_canonical_5m_current(last_candle_ts, now, timeframe=timeframe):
        classification = "healthy"
    elif not is_todays_rth_candle(last_candle_ts, now, timeframe=timeframe):
        classification = "stale"
    elif stale_since_close <= limit + 5:
        classification = "latency_ok"
    else:
        classification = "stale"

    return {
        **base,
        "classification": classification,
        "age_minutes": wall_age,
        "staleness_since_close_minutes": stale_since_close,
    }


def is_equity_strategy_candle_eligible(
    asset: AssetDefinition,
    last_candle_ts: datetime | None,
    now: datetime,
    *,
    timeframe: str = PROVIDER_TIMEFRAME,
) -> tuple[bool, str]:
    """Strategy evaluation gate — blocks pre-RTH 5m; warmup is not stale_data."""
    if not session_allows_entries(asset.trading_sessions, now):
        return False, "session_closed"

    if not is_fast_protection_equity(asset.db_symbol):
        from quantara_engine.market_data.strategy_freshness_health import (
            is_strategy_candle_eligible,
        )

        return is_strategy_candle_eligible(asset, last_candle_ts, now, timeframe=timeframe)

    if in_rth_warmup_phase(now, timeframe):
        return False, STRATEGY_STATUS_WARMUP

    if last_candle_ts is None:
        return False, "no_data"

    last_candle_ts = _as_utc(last_candle_ts)

    if not is_todays_rth_candle(last_candle_ts, now, timeframe=timeframe):
        stale_since_close = round(bar_staleness_minutes(last_candle_ts, timeframe, now), 1)
        limit = round(max_staleness_minutes(timeframe), 1)
        return False, f"stale_data ({stale_since_close}m since close, limit {limit}m)"

    if is_market_data_fresh(last_candle_ts, timeframe, now):
        return True, "eligible"

    stale_since_close = round(bar_staleness_minutes(last_candle_ts, timeframe, now), 1)
    limit = round(max_staleness_minutes(timeframe), 1)
    return False, f"stale_data ({stale_since_close}m since close, limit {limit}m)"


def resolve_equity_session_stale(
    asset: AssetDefinition | None,
    last_5m: datetime | None,
    now: datetime,
    *,
    last_1m: datetime | None = None,
) -> tuple[bool, str | None]:
    """
    Dashboard stale flag + optional status override for session-open assets.

    Returns (ui_stale, dashboard_status).
    """
    if asset is None or not is_fast_protection_equity(asset.db_symbol):
        from quantara_engine.market_data.sessions import is_data_stale_while_session_open

        sessions = asset.trading_sessions if asset else {}
        stale = is_data_stale_while_session_open(sessions, last_5m, now)
        return stale, ("stale" if stale else "healthy")

    feed = classify_equity_feed_health(last_1m=last_1m, last_5m=last_5m, now=now)
    return feed["ui_data_error"], feed["dashboard_status"]


def equity_symbols() -> frozenset[str]:
    return FAST_EQUITY_DB_SYMBOLS
