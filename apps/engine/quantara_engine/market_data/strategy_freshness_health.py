"""Session-aware strategy candle freshness — separate from 1m LIVE_MARK."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from quantara_engine.market_data.polling import (
    bar_staleness_minutes,
    is_market_data_fresh,
    max_staleness_minutes,
)
from quantara_engine.market_data.registry import AssetDefinition, get_asset
from quantara_engine.market_data.sessions import session_allows_entries


def _as_utc(ts: datetime) -> datetime:
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


def classify_strategy_candle_health(
    asset: AssetDefinition,
    last_candle_ts: datetime | None,
    now: datetime,
    *,
    timeframe: str = "5m",
    last_1m: datetime | None = None,
) -> dict[str, Any]:
    """
    Classify strategy candle freshness for dashboard health.

    Does not use 1m LIVE_MARK timestamps — only canonical strategy timeframe candles.
    US equities delegate to RTH session-open semantics (feed vs strategy readiness).
    """
    from quantara_engine.execution.crypto_mark_valuation import (
        is_fast_protection_crypto,
        is_fast_protection_equity,
    )
    from quantara_engine.market_data.equity_rth_health import (
        classify_equity_strategy_timeframe_health,
    )

    if is_fast_protection_crypto(asset.db_symbol):
        now = _as_utc(now)
        session_open = session_allows_entries(asset.trading_sessions, now)
        limit_1m = round(max_staleness_minutes("1m"), 1)
        protection_1m_stale = False
        protection_1m_age: float | None = None
        last_1m_utc = _as_utc(last_1m) if last_1m is not None else None
        if last_1m_utc is not None:
            protection_1m_age = round((now - last_1m_utc).total_seconds() / 60, 1)
            protection_1m_stale = not is_market_data_fresh(last_1m_utc, "1m", now)
        elif session_open:
            protection_1m_stale = True

        if last_candle_ts is None:
            classification = "missing" if session_open else "session_closed"
            wall_age = None
            stale_since_close = None
            last_iso = None
        else:
            last_candle_ts = _as_utc(last_candle_ts)
            wall_age = round((now - last_candle_ts).total_seconds() / 60, 1)
            stale_since_close = round(bar_staleness_minutes(last_candle_ts, timeframe, now), 1)
            last_iso = last_candle_ts.isoformat()
            if not session_open:
                classification = "session_closed"
            elif is_market_data_fresh(last_candle_ts, timeframe, now):
                classification = "healthy"
            elif stale_since_close <= round(max_staleness_minutes(timeframe), 1) + 5:
                classification = "latency_ok"
            else:
                classification = "stale"

        if session_open and protection_1m_stale:
            classification = "stale"

        return {
            "classification": classification,
            "session_open": session_open,
            "age_minutes": wall_age,
            "staleness_since_close_minutes": stale_since_close,
            "freshness_limit_minutes": round(max_staleness_minutes(timeframe), 1),
            "last_candle_at": last_iso,
            "protection_1m_authority": {
                "last_candle_at": last_1m_utc.isoformat() if last_1m_utc else None,
                "age_minutes": protection_1m_age,
                "freshness_limit_minutes": limit_1m,
                "stale": protection_1m_stale,
            },
            "protection_1m_stale": protection_1m_stale,
        }

    if is_fast_protection_equity(asset.db_symbol):
        return classify_equity_strategy_timeframe_health(
            asset,
            last_candle_ts,
            now,
            timeframe=timeframe,
            last_1m=last_1m,
        )

    now = _as_utc(now)
    session_open = session_allows_entries(asset.trading_sessions, now)

    if last_candle_ts is None:
        return {
            "classification": "missing" if session_open else "session_closed",
            "session_open": session_open,
            "age_minutes": None,
            "staleness_since_close_minutes": None,
            "freshness_limit_minutes": round(max_staleness_minutes(timeframe), 1),
            "last_candle_at": None,
        }

    last_candle_ts = _as_utc(last_candle_ts)
    wall_age = round((now - last_candle_ts).total_seconds() / 60, 1)
    stale_since_close = round(bar_staleness_minutes(last_candle_ts, timeframe, now), 1)
    limit = round(max_staleness_minutes(timeframe), 1)

    if not session_open:
        classification = "session_closed"
    elif is_market_data_fresh(last_candle_ts, timeframe, now):
        classification = "healthy"
    elif stale_since_close <= limit + 5:
        classification = "latency_ok"
    else:
        classification = "stale"

    return {
        "classification": classification,
        "session_open": session_open,
        "age_minutes": wall_age,
        "staleness_since_close_minutes": stale_since_close,
        "freshness_limit_minutes": limit,
        "last_candle_at": last_candle_ts.isoformat(),
    }


def is_strategy_candle_eligible(
    asset: AssetDefinition,
    last_candle_ts: datetime | None,
    now: datetime,
    *,
    timeframe: str = "5m",
) -> tuple[bool, str]:
    """Session-aware replacement for raw is_market_data_fresh in strategy eligibility."""
    from quantara_engine.execution.crypto_mark_valuation import is_fast_protection_equity
    from quantara_engine.market_data.equity_rth_health import is_equity_strategy_candle_eligible

    if is_fast_protection_equity(asset.db_symbol):
        return is_equity_strategy_candle_eligible(
            asset, last_candle_ts, now, timeframe=timeframe
        )

    if not session_allows_entries(asset.trading_sessions, now):
        return False, "session_closed"

    if last_candle_ts is None:
        return False, "no_data"

    if is_market_data_fresh(last_candle_ts, timeframe, now):
        return True, "eligible"

    stale_since_close = round(bar_staleness_minutes(last_candle_ts, timeframe, now), 1)
    limit = round(max_staleness_minutes(timeframe), 1)
    return False, f"stale_data ({stale_since_close}m since close, limit {limit}m)"


def aggregate_market_health(
    per_asset: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Summarize per-asset health into global market data status for strategy UI."""
    open_assets = [sym for sym, row in per_asset.items() if row.get("session_open")]
    stale_open = [
        sym
        for sym in open_assets
        if per_asset[sym].get("classification") in ("stale", "missing")
        or per_asset[sym].get("protection_1m_stale")
    ]
    closed = [
        sym
        for sym, row in per_asset.items()
        if row.get("classification") == "session_closed"
    ]

    if stale_open:
        status = "market_stale"
    elif open_assets:
        status = "healthy"
    else:
        status = "session_closed"

    return {
        "market_health_status": status,
        "stale_open_assets": stale_open,
        "session_closed_assets": closed,
    }


def health_for_symbol(db_symbol: str, last_candle_ts: datetime | None, now: datetime) -> dict[str, Any]:
    asset = get_asset(db_symbol)
    if not asset:
        return {"classification": "unknown", "session_open": False, "age_minutes": None}
    row = classify_strategy_candle_health(asset, last_candle_ts, now)
    row["db_symbol"] = db_symbol
    row["display_symbol"] = asset.display_symbol
    return row
