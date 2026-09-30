#!/usr/bin/env python3
"""Serial V3.1 discovery when V3 ROBUST=0 — separate checkpoint."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps" / "engine"))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from quantara_engine.persistence.store import TradingStore
from quantara_engine.research.v3.candidates_v31 import frozen_v31_candidates
from quantara_engine.research.v3.checkpoint import append_result, load_completed, result_key
from quantara_engine.research.v3.constants import V3_RESEARCH_START
from quantara_engine.research.v3.data_quality_gate import build_data_quality_matrix
from quantara_engine.research.v3.discovery import _skip_backtest, _test_pairs, run_v3_job
from quantara_engine.strategies.registry import get_latest

RESEARCH = ROOT / "scripts" / "research"
CHECKPOINT = RESEARCH / "v3_1_discovery_checkpoint.jsonl"
REPORT = RESEARCH / "v3_1_discovery_report.json"


def db_url() -> str:
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("DATABASE_URL="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise RuntimeError("DATABASE_URL missing")


def main() -> None:
    session = sessionmaker(bind=create_engine(db_url()))()
    store = TradingStore(session)
    quality = build_data_quality_matrix(store)
    pairs = _test_pairs(quality)
    candidates = frozen_v31_candidates()
    completed = load_completed(CHECKPOINT)
    results = list(completed.values())

    for candidate in candidates:
        cls = get_latest(candidate.strategy_slug)
        for symbol, timeframe in pairs:
            if _skip_backtest(candidate, symbol, timeframe):
                continue
            if timeframe not in cls.supported_timeframes():
                continue
            key = result_key(candidate.candidate_id, symbol, timeframe)
            if key in completed:
                continue
            instrument = store.get_instrument_by_symbol(symbol)
            if not instrument:
                continue
            candles = [
                c
                for c in store.list_candles(instrument.id, timeframe)
                if c.timestamp >= V3_RESEARCH_START
            ]
            row = run_v3_job(
                store,
                candidate=candidate,
                symbol=symbol,
                timeframe=timeframe,
                candles=candles,
            )
            if row:
                append_result(CHECKPOINT, row)
                completed[key] = row
                results.append(row)

    robust_n = len([r for r in results if r.get("robustness") == "ROBUST"])
    report = {
        "candidates_tested": len(results),
        "robustness_counts": {
            "ROBUST": robust_n,
            "PROMISING": len([r for r in results if r.get("robustness") == "PROMISING"]),
        },
        "top10": sorted(
            results,
            key=lambda r: (r.get("robustness") != "ROBUST", -(r.get("oos") or {}).get("expectancy_r") or -999),
        )[:10],
        "gate": "FAIL" if robust_n == 0 else "PASS",
    }
    REPORT.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    session.close()
    print(json.dumps({"ok": True, "tested": len(results), "robust": robust_n}, indent=2))


if __name__ == "__main__":
    main()
