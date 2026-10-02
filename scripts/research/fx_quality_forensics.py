#!/usr/bin/env python3
"""EURUSD/USDJPY quality gate forensics + optional 5m->15m/1h derive."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps" / "engine"))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from quantara_workers.jobs.fetch_data import _derive_full
from quantara_engine.persistence.store import TradingStore
from quantara_engine.research.v3.constants import V3_RESEARCH_START
from quantara_engine.research.v3.data_quality_gate import assess_candle_quality, build_data_quality_matrix
from quantara_engine.research.v3.dataset import _load_contamination_windows
from quantara_engine.research.v3.discovery import _test_pairs


def db_url() -> str:
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("DATABASE_URL="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise RuntimeError("DATABASE_URL missing")


def sym_report(store: TradingStore, sym: str, *, derive: bool) -> dict:
    inst = store.get_instrument_by_symbol(sym)
    if not inst:
        return {"error": "no_instrument"}
    if derive:
        _derive_full(store, str(inst.id), session_mode="utc")
        store.session.commit()

    contamination = _load_contamination_windows(store)
    exclude = []
    cfg = contamination.get(sym) or contamination.get(sym.upper())
    if isinstance(cfg, dict) and cfg.get("start_il") and cfg.get("end_il"):
        exclude = [
            (
                datetime.fromisoformat(str(cfg["start_il"]).replace("Z", "+00:00")),
                datetime.fromisoformat(str(cfg["end_il"]).replace("Z", "+00:00")),
            )
        ]

    out: dict = {"symbol": sym, "timeframes": {}}
    for tf in ("5m", "15m", "1h"):
        counts = store.count_candles(inst.id, tf)
        latest = store.latest_candle_timestamp(inst.id, tf)
        v = assess_candle_quality(
            store,
            symbol=sym,
            timeframe=tf,
            exclude_ranges=exclude,
            research_start=V3_RESEARCH_START,
        )
        out["timeframes"][tf] = {
            "bar_count": counts,
            "latest": latest.isoformat() if latest else None,
            "classification": v.classification,
            "completeness_pct": v.completeness_pct,
            "expected_bars": v.expected_bars,
            "actual_bars": v.actual_bars,
            "missing_bars": v.missing_bars,
            "duplicate_bars": v.duplicate_bars,
            "out_of_order": v.out_of_order,
            "reject_reason": v.notes,
        }
    return out


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--derive", action="store_true", help="run canonical 5m->15m/1h derive before assess")
    args = ap.parse_args()

    session = sessionmaker(bind=create_engine(db_url(), pool_pre_ping=True))()
    store = TradingStore(session)
    reports = {sym: sym_report(store, sym, derive=args.derive) for sym in ("EURUSD", "USDJPY")}
    quality = build_data_quality_matrix(store)
    allowed = set(_test_pairs(quality))
    for sym in ("EURUSD", "USDJPY"):
        reports[sym]["research_allowed_pairs"] = [
            tf for tf in ("5m", "15m", "1h") if (sym, tf) in allowed
        ]
    session.close()
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "derived_before_assess": args.derive,
        "reports": reports,
    }
    out = ROOT / "scripts/research/fx_quality_forensics_report.json"
    out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
