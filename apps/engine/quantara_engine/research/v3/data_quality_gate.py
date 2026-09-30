"""Session-aware candle quality gate for V3 research."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import text

from quantara_engine.market_data.registry import get_asset
from quantara_engine.market_data.sessions import session_allows_entries
from quantara_engine.persistence.store import TradingStore
from quantara_engine.research.v3.constants import V3_RESEARCH_ASSETS, V3_RESEARCH_START
from quantara_engine.research.v3.dataset import _load_contamination_windows

TF_MINUTES = {"1m": 1, "5m": 5, "15m": 15, "1h": 60}


@dataclass(frozen=True)
class QualityVerdict:
    symbol: str
    timeframe: str
    classification: str  # GOOD | WATCH | REJECT
    completeness_pct: float | None
    expected_bars: int
    actual_bars: int
    missing_bars: int
    duplicate_bars: int
    out_of_order: int
    notes: str


def _is_24x7(sessions: dict) -> bool:
    raw = sessions.get("sessions") if isinstance(sessions, dict) else None
    if not raw:
        return True
    return any(str(s).lower() in ("24x7", "24/7", "always") for s in raw)


def _expected_session_bars(
    *,
    start: datetime,
    end: datetime,
    timeframe: str,
    symbol: str,
) -> int:
    step_min = TF_MINUTES[timeframe]
    step = timedelta(minutes=step_min)
    asset = get_asset(symbol.upper().replace("/", ""))
    sessions = asset.trading_sessions if asset else {"sessions": ["24x7"]}
    if _is_24x7(sessions):
        seconds = max(0.0, (end - start).total_seconds())
        return max(1, int(seconds // (step_min * 60)) + 1)

    count = 0
    t = start
    while t <= end:
        if session_allows_entries(sessions, t):
            count += 1
        t += step
    return max(count, 1)


def assess_candle_quality(
    store: TradingStore,
    *,
    symbol: str,
    timeframe: str,
    exclude_ranges: list[tuple[datetime, datetime]],
    research_start: datetime,
) -> QualityVerdict:
    sym = symbol.upper().replace("/", "")
    iid = store.session.execute(
        text("SELECT id::text FROM instruments WHERE symbol = :sym"),
        {"sym": sym},
    ).scalar()
    if not iid:
        return QualityVerdict(
            sym, timeframe, "REJECT", None, 0, 0, 0, 0, 0, "instrument_missing"
        )

    agg = store.session.execute(
        text(
            """
            SELECT
              COUNT(*)::int AS n,
              COUNT(DISTINCT timestamp)::int AS n_distinct,
              MIN(timestamp) AS start_ts,
              MAX(timestamp) AS end_ts
            FROM candles
            WHERE instrument_id = CAST(:iid AS uuid)
              AND timeframe = :tf
              AND timestamp >= :research_start
            """
        ),
        {"iid": iid, "tf": timeframe, "research_start": research_start},
    ).mappings().first()

    n = int(agg["n"] or 0) if agg else 0
    n_distinct = int(agg["n_distinct"] or 0) if agg else 0
    start = agg["start_ts"] if agg else None
    end = agg["end_ts"] if agg else None

    if not n or start is None or end is None:
        return QualityVerdict(sym, timeframe, "REJECT", 0.0, 0, 0, 0, 0, 0, "no_bars")

    for ex_start, ex_end in exclude_ranges:
        if ex_start <= end and ex_end >= start:
            excluded = store.session.execute(
                text(
                    """
                    SELECT COUNT(*)::int FROM candles
                    WHERE instrument_id = CAST(:iid AS uuid)
                      AND timeframe = :tf
                      AND timestamp >= :research_start
                      AND timestamp >= :ex_start
                      AND timestamp <= :ex_end
                    """
                ),
                {
                    "iid": iid,
                    "tf": timeframe,
                    "research_start": research_start,
                    "ex_start": ex_start,
                    "ex_end": ex_end,
                },
            ).scalar()
            n = max(0, n - int(excluded or 0))
            n_distinct = min(n_distinct, n)

    ooo = store.session.execute(
        text(
            """
            SELECT COUNT(*)::int FROM (
              SELECT timestamp,
                     LAG(timestamp) OVER (ORDER BY timestamp) AS prev_ts
              FROM candles
              WHERE instrument_id = CAST(:iid AS uuid)
                AND timeframe = :tf
                AND timestamp >= :research_start
            ) x
            WHERE prev_ts IS NOT NULL AND timestamp <= prev_ts
            """
        ),
        {"iid": iid, "tf": timeframe, "research_start": research_start},
    ).scalar() or 0

    dup = max(0, n - n_distinct)
    window_start = max(start, research_start)
    expected = _expected_session_bars(
        start=window_start, end=end, timeframe=timeframe, symbol=sym
    )
    actual = n_distinct
    missing = max(0, expected - actual)
    completeness = round(actual / expected * 100, 2) if expected else 0.0

    classification = "GOOD"
    notes = ""
    if completeness < 70:
        classification = "REJECT"
        notes = "completeness_below_70"
    elif completeness < 90 or ooo > 0 or dup > 0:
        classification = "WATCH"
        parts = []
        if completeness < 90:
            parts.append(f"completeness_{completeness}")
        if missing:
            parts.append(f"missing_{missing}")
        if dup:
            parts.append(f"dup_{dup}")
        if ooo:
            parts.append(f"ooo_{ooo}")
        notes = ";".join(parts) or "gaps_or_duplicates_or_completeness_70_90"

    return QualityVerdict(
        symbol=sym,
        timeframe=timeframe,
        classification=classification,
        completeness_pct=completeness,
        expected_bars=expected,
        actual_bars=actual,
        missing_bars=missing,
        duplicate_bars=dup,
        out_of_order=int(ooo),
        notes=notes,
    )


def build_data_quality_matrix(store: TradingStore) -> dict[str, Any]:
    contamination = _load_contamination_windows(store)
    exclude_by_sym: dict[str, list[tuple[datetime, datetime]]] = {}
    for sym, cfg in contamination.items():
        if not isinstance(cfg, dict):
            continue
        start_s = cfg.get("start_il")
        end_s = cfg.get("end_il")
        if start_s and end_s:
            exclude_by_sym[sym.upper()] = [
                (
                    datetime.fromisoformat(str(start_s).replace("Z", "+00:00")),
                    datetime.fromisoformat(str(end_s).replace("Z", "+00:00")),
                )
            ]

    matrix: list[dict] = []
    for sym in V3_RESEARCH_ASSETS:
        for tf in ("1m", "5m", "15m", "1h"):
            v = assess_candle_quality(
                store,
                symbol=sym,
                timeframe=tf,
                exclude_ranges=exclude_by_sym.get(sym.upper(), []),
                research_start=V3_RESEARCH_START,
            )
            matrix.append(v.__dict__)

    good = [m for m in matrix if m["classification"] == "GOOD"]
    watch = [m for m in matrix if m["classification"] == "WATCH"]
    reject = [m for m in matrix if m["classification"] == "REJECT"]
    return {
        "research_start": V3_RESEARCH_START.isoformat(),
        "matrix": matrix,
        "good_count": len(good),
        "watch_count": len(watch),
        "reject_count": len(reject),
        "good_pairs": [(m["symbol"], m["timeframe"]) for m in good],
        "watch_pairs": [(m["symbol"], m["timeframe"]) for m in watch],
        "reject_pairs": [(m["symbol"], m["timeframe"]) for m in reject],
    }
