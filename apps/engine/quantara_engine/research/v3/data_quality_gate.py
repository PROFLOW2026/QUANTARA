"""Session-aware candle quality gate for V3 research."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import text

from quantara_engine.market_data.registry import get_asset
from quantara_engine.market_data.sessions import session_allows_entries
from quantara_engine.persistence.store import TradingStore
from quantara_engine.research.v3.constants import V3_RESEARCH_ASSETS
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


def _expected_session_bars(
    *,
    start: datetime,
    end: datetime,
    timeframe: str,
    symbol: str,
) -> int:
    step = timedelta(minutes=TF_MINUTES[timeframe])
    asset = get_asset(symbol.upper().replace("/", ""))
    sessions = asset.sessions if asset else {"sessions": ["24x7"]}
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

    rows = store.session.execute(
        text(
            """
            SELECT timestamp, is_complete
            FROM candles
            WHERE instrument_id = CAST(:iid AS uuid) AND timeframe = :tf
            ORDER BY timestamp
            """
        ),
        {"iid": iid, "tf": timeframe},
    ).mappings().all()
    if not rows:
        return QualityVerdict(sym, timeframe, "REJECT", 0.0, 0, 0, 0, 0, 0, "no_bars")

    ts_list = [r["timestamp"] for r in rows]
    start, end = ts_list[0], ts_list[-1]
    for ex_start, ex_end in exclude_ranges:
        ts_list = [t for t in ts_list if not (ex_start <= t <= ex_end)]

    step = timedelta(minutes=TF_MINUTES[timeframe])
    dup = len(rows) - len({r["timestamp"] for r in rows})
    ooo = sum(1 for i in range(1, len(ts_list)) if ts_list[i] <= ts_list[i - 1])
    expected = _expected_session_bars(start=start, end=end, timeframe=timeframe, symbol=sym)
    actual = len(ts_list)
    missing = max(0, expected - actual)
    completeness = round(actual / expected * 100, 2) if expected else 0.0

    classification = "GOOD"
    notes = ""
    if completeness < 70:
        classification = "REJECT"
        notes = "completeness_below_70"
    elif completeness < 90 or ooo > 0 or dup > 0:
        classification = "WATCH"
        notes = "gaps_or_duplicates_or_completeness_70_90"

    return QualityVerdict(
        symbol=sym,
        timeframe=timeframe,
        classification=classification,
        completeness_pct=completeness,
        expected_bars=expected,
        actual_bars=actual,
        missing_bars=missing,
        duplicate_bars=dup,
        out_of_order=ooo,
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
            )
            matrix.append(v.__dict__)

    good = [m for m in matrix if m["classification"] == "GOOD"]
    reject = [m for m in matrix if m["classification"] == "REJECT"]
    return {
        "matrix": matrix,
        "good_count": len(good),
        "reject_count": len(reject),
        "good_pairs": [(m["symbol"], m["timeframe"]) for m in good],
        "reject_pairs": [(m["symbol"], m["timeframe"]) for m in reject],
    }
