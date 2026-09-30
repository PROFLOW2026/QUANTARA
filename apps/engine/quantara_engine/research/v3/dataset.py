"""Canonical research dataset quality report."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import text

from quantara_engine.persistence.store import TradingStore
from quantara_engine.research.v3.constants import (
    EQUITY_1M_CONTAMINATION_SETTINGS_KEY,
    V3_RESEARCH_ASSETS,
)

TIMEFRAMES: tuple[str, ...] = ("1m", "5m", "15m", "1h")


def _load_contamination_windows(store: TradingStore) -> dict[str, Any]:
    raw = store.get_settings_dict().get(EQUITY_1M_CONTAMINATION_SETTINGS_KEY)
    if not isinstance(raw, dict):
        return {}
    return dict(raw.get("symbols") or {})


def _gap_periods(
    timestamps: list[datetime],
    *,
    expected_delta: timedelta,
    tolerance: timedelta,
) -> list[dict[str, str]]:
    gaps: list[dict[str, str]] = []
    for i in range(1, len(timestamps)):
        delta = timestamps[i] - timestamps[i - 1]
        if delta > expected_delta + tolerance:
            gaps.append(
                {
                    "from": timestamps[i - 1].isoformat(),
                    "to": timestamps[i].isoformat(),
                    "gap_hours": round(delta.total_seconds() / 3600, 2),
                }
            )
    return gaps[:50]


def asset_candle_quality(
    store: TradingStore,
    *,
    symbol: str,
    contamination: dict[str, Any],
) -> dict[str, Any]:
    sym = symbol.upper().replace("/", "")
    row = store.session.execute(
        text("SELECT id::text FROM instruments WHERE symbol = :sym"),
        {"sym": sym},
    ).scalar()
    if not row:
        return {"symbol": sym, "error": "instrument_missing"}

    instrument_id = row
    out: dict[str, Any] = {"symbol": sym, "timeframes": {}}
    global_start: datetime | None = None
    global_end: datetime | None = None

    tf_delta = {
        "1m": timedelta(minutes=1),
        "5m": timedelta(minutes=5),
        "15m": timedelta(minutes=15),
        "1h": timedelta(hours=1),
    }

    for tf in TIMEFRAMES:
        bounds = store.session.execute(
            text(
                """
                SELECT MIN(timestamp) AS start_ts, MAX(timestamp) AS end_ts, COUNT(*)::int AS n
                FROM candles
                WHERE instrument_id = CAST(:iid AS uuid) AND timeframe = :tf
                """
            ),
            {"iid": instrument_id, "tf": tf},
        ).mappings().first()
        n = int(bounds["n"] or 0) if bounds else 0
        start_ts = bounds["start_ts"] if bounds else None
        end_ts = bounds["end_ts"] if bounds else None
        if start_ts and (global_start is None or start_ts < global_start):
            global_start = start_ts
        if end_ts and (global_end is None or end_ts > global_end):
            global_end = end_ts

        gaps: list[dict] = []
        if n > 1 and tf in tf_delta:
            ts_rows = store.session.execute(
                text(
                    """
                    SELECT timestamp FROM candles
                    WHERE instrument_id = CAST(:iid AS uuid) AND timeframe = :tf
                    ORDER BY timestamp
                    """
                ),
                {"iid": instrument_id, "tf": tf},
            ).scalars().all()
            gaps = _gap_periods(
                list(ts_rows),
                expected_delta=tf_delta[tf],
                tolerance=tf_delta[tf],
            )

        expected_bars = None
        completeness_pct = None
        if start_ts and end_ts and tf in tf_delta and n > 0:
            span = end_ts - start_ts
            expected_bars = max(1, int(span / tf_delta[tf]) + 1)
            completeness_pct = round(min(100.0, n / expected_bars * 100), 2)

        out["timeframes"][tf] = {
            "history_start": start_ts.isoformat() if start_ts else None,
            "history_end": end_ts.isoformat() if end_ts else None,
            "bar_count": n,
            "completeness_pct": completeness_pct,
            "large_gaps_sample": gaps[:10],
            "large_gap_count": len(gaps),
        }

    contam = contamination.get(sym) or contamination.get(symbol)
    exclude_ranges: list[dict] = []
    if isinstance(contam, dict) and contam.get("exclude_from_strategy_stats"):
        exclude_ranges.append(
            {
                "start": contam.get("start_il"),
                "end": contam.get("end_il"),
                "reason": "equity_1m_contamination",
            }
        )

    quality = "acceptable"
    if global_start is None:
        quality = "missing"
    elif any(
        (out["timeframes"].get(tf) or {}).get("completeness_pct") is not None
        and (out["timeframes"][tf]["completeness_pct"] or 0) < 85
        for tf in ("5m", "15m")
    ):
        quality = "watch"

    out.update(
        {
            "history_start": global_start.isoformat() if global_start else None,
            "history_end": global_end.isoformat() if global_end else None,
            "excluded_periods": exclude_ranges,
            "tradable_sample_quality": quality,
        }
    )
    return out


def build_canonical_dataset_report(store: TradingStore) -> dict[str, Any]:
    contamination = _load_contamination_windows(store)
    assets = [
        asset_candle_quality(store, symbol=s, contamination=contamination)
        for s in V3_RESEARCH_ASSETS
    ]
    starts = [a.get("history_start") for a in assets if a.get("history_start")]
    ends = [a.get("history_end") for a in assets if a.get("history_end")]
    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "assets": assets,
        "dataset_period": {
            "start": min(starts) if starts else None,
            "end": max(ends) if ends else None,
        },
        "contamination_policy": "equity_1m windows excluded from strategy stats where configured",
    }
