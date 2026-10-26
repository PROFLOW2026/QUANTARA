"""Lightweight Live Sim market scan over the active polling universe."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import text

from quantara_engine.market_data.active_universe import list_active_db_symbols
from quantara_engine.persistence.store import TradingStore

SCAN_SETTINGS_KEY = "live_sim_market_scan"
SCAN_MIN_INTERVAL_MINUTES = 30


def _score_symbol(session, symbol: str, *, now: datetime) -> dict[str, Any] | None:
    since = now - timedelta(days=5)
    row = session.execute(
        text(
            """
            WITH bars AS (
              SELECT c.close, c.volume, c.timestamp
              FROM candles c
              JOIN instruments i ON i.id = c.instrument_id
              WHERE i.symbol = :sym AND c.timeframe = '1h'
                AND c.timestamp >= :since
              ORDER BY c.timestamp DESC
              LIMIT 48
            ),
            agg AS (
              SELECT
                COUNT(*) AS n,
                AVG(volume) AS avg_vol,
                MAX(volume) AS max_vol,
                (ARRAY_AGG(close ORDER BY timestamp DESC))[1] AS last_close,
                (ARRAY_AGG(close ORDER BY timestamp DESC))[2] AS prev_close,
                (ARRAY_AGG(close ORDER BY timestamp ASC))[1] AS first_close
              FROM bars
            )
            SELECT * FROM agg
            """
        ),
        {"sym": symbol, "since": since},
    ).mappings().first()
    if not row or int(row["n"] or 0) < 12:
        return None
    last = Decimal(str(row["last_close"] or 0))
    prev = Decimal(str(row["prev_close"] or 0))
    first = Decimal(str(row["first_close"] or 0))
    if last <= 0 or first <= 0:
        return None
    day_move_pct = float((last - prev) / prev * 100) if prev else 0.0
    range_move_pct = float((last - first) / first * 100)
    avg_vol = float(row["avg_vol"] or 1)
    max_vol = float(row["max_vol"] or 0)
    vol_ratio = max_vol / avg_vol if avg_vol > 0 else 0.0
    momentum_score = abs(day_move_pct) * 2 + abs(range_move_pct) + min(vol_ratio, 5) * 3
    return {
        "symbol": symbol,
        "day_move_pct": round(day_move_pct, 3),
        "range_move_pct": round(range_move_pct, 3),
        "volume_spike_ratio": round(vol_ratio, 3),
        "score": round(momentum_score, 3),
    }


def rank_active_universe(store: TradingStore, *, limit: int = 12) -> list[dict[str, Any]]:
    now = datetime.now(timezone.utc)
    ranked: list[dict[str, Any]] = []
    for sym in list_active_db_symbols():
        scored = _score_symbol(store.session, sym, now=now)
        if scored:
            ranked.append(scored)
    ranked.sort(key=lambda x: x["score"], reverse=True)
    return ranked[:limit]


def maybe_refresh_live_sim_market_scan(store: TradingStore) -> dict[str, Any] | None:
    """Throttled scan persisted to settings for UI / allocation hints."""
    settings = store.get_settings_dict()
    prev = dict(settings.get(SCAN_SETTINGS_KEY) or {})
    last_at = prev.get("updated_at")
    now = datetime.now(timezone.utc)
    if last_at:
        try:
            last = datetime.fromisoformat(str(last_at).replace("Z", "+00:00"))
            if (now - last).total_seconds() < SCAN_MIN_INTERVAL_MINUTES * 60:
                return None
        except ValueError:
            pass
    from quantara_engine.live_sim.dynamic_universe import sync_dynamic_live_sim_universe

    ranked = rank_active_universe(store)
    universe = sync_dynamic_live_sim_universe(store)
    payload = {
        "updated_at": now.isoformat(),
        "top_momentum": ranked[:6],
        "top_breakout_candidates": sorted(ranked, key=lambda x: abs(x["day_move_pct"]), reverse=True)[:6],
        "top_volume": sorted(ranked, key=lambda x: x["volume_spike_ratio"], reverse=True)[:6],
        "dynamic_universe": universe,
    }
    store.update_settings(SCAN_SETTINGS_KEY, payload)
    return payload
