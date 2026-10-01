#!/usr/bin/env python3
"""Run realistic replay for all V3.1 ROBUST candidates."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps" / "engine"))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from quantara_engine.persistence.store import TradingStore
from quantara_engine.research.v3.constants import V3_RESEARCH_START
from quantara_engine.research.v3_1.realistic_replay import (
    ENTRY_MODEL,
    monte_carlo_r,
    portfolio_chronological,
    replay_candidate,
)

OUT = ROOT / "scripts" / "research" / "v3_1_realistic_replay_report.json"
FINAL = ROOT / "scripts" / "research" / "v3_1_final_outcome.json"


def db_url() -> str:
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("DATABASE_URL="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise RuntimeError("DATABASE_URL missing")


def _entry_overlap_pct(a: list[str], b: list[str], *, window_minutes: int = 15) -> float:
    from datetime import datetime

    if not a or not b:
        return 0.0
    bt = [datetime.fromisoformat(x.replace("Z", "+00:00")) for x in b]
    hits = 0
    for sa in a:
        ta = datetime.fromisoformat(sa.replace("Z", "+00:00"))
        if any(abs((ta - tb).total_seconds()) <= window_minutes * 60 for tb in bt):
            hits += 1
    return round(hits / len(a) * 100, 2)


def main() -> None:
    outcome = json.loads(FINAL.read_text(encoding="utf-8"))
    robust = outcome.get("robust_candidates") or []
    session = sessionmaker(bind=create_engine(db_url()))()
    store = TradingStore(session)
    results: list[dict] = []

    for i, row in enumerate(robust, 1):
        print(f"replay {i}/{len(robust)} {row['key']}", flush=True)
        inst = store.get_instrument_by_symbol(row["asset"])
        if not inst:
            continue
        candles = [
            c for c in store.list_candles(inst.id, row["timeframe"]) if c.timestamp >= V3_RESEARCH_START
        ]
        rep = replay_candidate(
            store,
            key=row["key"],
            family=row["family"],
            asset=row["asset"],
            timeframe=row["timeframe"],
            direction=row["direction"],
            parameters=row.get("parameters") or {},
            instrument=inst,
            candles=candles,
        )
        results.append(rep)

    session.close()

    dedup_pairs = [
        ("atr_trend|v1|AMD|15m|long", "atr_trend|v2|AMD|15m|long"),
        ("atr_trend|v1|NVDA|15m|long", "atr_trend|v2|NVDA|15m|long"),
        ("vwap_mean_reversion|v1|AMD|5m|long", "vwap_mean_reversion|v2|AMD|5m|long"),
    ]
    by_key = {r["key"]: r for r in results}
    dedup_notes = []
    for a, b in dedup_pairs:
        if a in by_key and b in by_key:
            dedup_notes.append(
                {
                    "a": a,
                    "b": b,
                    "entry_overlap_pct": _entry_overlap_pct(
                        by_key[a].get("entry_timestamps") or [],
                        by_key[b].get("entry_timestamps") or [],
                    ),
                }
            )

    survivors = [r for r in results if r.get("classification") == "SURVIVES"]
    degraded = [r for r in results if r.get("classification") == "DEGRADED_BUT_VALID"]
    failed = [r for r in results if r.get("classification") == "FAIL"]
    data_rej = [r for r in results if r.get("classification") == "DATA_REJECT"]

    drop_keys: set[str] = set()
    for note in dedup_notes:
        if note["entry_overlap_pct"] >= 50:
            a, b = note["a"], note["b"]
            ra, rb = by_key[a], by_key[b]
            ea = (ra.get("realistic_oos") or {}).get("expectancy_r") or -999
            eb = (rb.get("realistic_oos") or {}).get("expectancy_r") or -999
            drop_keys.add(a if ea < eb else b)

    pool = [r for r in results if r.get("classification") in ("SURVIVES", "DEGRADED_BUT_VALID") and r["key"] not in drop_keys]
    pool.sort(key=lambda r: -((r.get("realistic_oos") or {}).get("expectancy_r") or -999))
    portfolio = pool[:6]

    port_rows = []
    for leg in portfolio:
        for row in leg.get("portfolio_trade_rows") or []:
            port_rows.append(row)
    port_sim = portfolio_chronological(port_rows)
    mc = monte_carlo_r(port_sim.get("trade_r_outcomes") or [], iterations=5000)

    # strip heavy fields
    for r in results:
        r.pop("portfolio_trade_rows", None)
        r.pop("entry_timestamps", None)

    report = {
        "entry_model": ENTRY_MODEL,
        "execution_note": "BacktestRunner + CandleProcessor + execution_assumptions_for (canonical QUANTARA backtest path). realistic_broker_v1 order lifecycle not yet wired into BacktestRunner — NEEDS_REALISTIC_REPLAY for full broker engine.",
        "candidates": results,
        "deduplication": dedup_notes,
        "near_duplicates_removed": sorted(drop_keys),
        "survivors_count": len(survivors),
        "degraded_count": len(degraded),
        "failed_count": len(failed),
        "data_rejected_count": len(data_rej),
        "distinct_portfolio": [{k: v for k, v in p.items() if k not in ("portfolio_trade_rows", "entry_timestamps")} for p in portfolio],
        "portfolio_sim_10k_0_25": {k: v for k, v in port_sim.items() if k != "trade_r_outcomes"},
        "monte_carlo_5000": mc,
        "realistic_edge_confirmed": len(survivors) > 0,
    }
    OUT.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(json.dumps({"ok": True, "path": str(OUT), "survivors": len(survivors), "portfolio": len(portfolio)}, indent=2))


if __name__ == "__main__":
    main()
