#!/usr/bin/env python3
"""V3.1 autonomous: Stage A → Stage B → portfolio → MC → $10k (local only)."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps" / "engine"))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from quantara_engine.persistence.store import TradingStore
from quantara_engine.research.v3.checkpoint import load_completed
from quantara_engine.research.v3.constants import V3_RESEARCH_START
from quantara_engine.research.v3.orchestration import (
    finalize_with_retry,
    unique_completed_count,
    wait_for_checkpoint_complete,
    write_authoritative_progress,
)
from quantara_engine.research.v3_1.job_plan import plan_v31_jobs, V31Job
from quantara_engine.research.v3_1.stage_b import portfolio_monte_carlo_and_10k, validate_survivor

RESEARCH = ROOT / "scripts" / "research"
CHECKPOINT = RESEARCH / "v3_1_discovery_checkpoint.jsonl"
REGISTRY = RESEARCH / "v3_1_discovery_jobs.json"
PROGRESS = RESEARCH / "v3_1_discovery_progress.json"
LOG = RESEARCH / "v3_1_autonomous_watch.log"
OUTCOME = RESEARCH / "v3_1_final_outcome.json"
PLAN = RESEARCH / "v3_1_research_plan_v32.json"


def log(msg: str) -> None:
    line = f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {msg}\n"
    LOG.open("a", encoding="utf-8").write(line)
    print(line, end="")


def db_url() -> str:
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("DATABASE_URL="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise RuntimeError("DATABASE_URL missing")


def run_stage_b() -> dict:
    session = sessionmaker(bind=create_engine(db_url()))()
    store = TradingStore(session)
    jobs = {j.key: j for j in plan_v31_jobs(store)}
    rows = load_completed(CHECKPOINT)
    survivors = [r for r in rows.values() if r.get("stage_a_pass")]
    validated: list[dict] = []
    for row in survivors:
        job = jobs.get(row["key"])
        if not job:
            continue
        inst = store.get_instrument_by_symbol(job.asset)
        if not inst:
            continue
        candles = [c for c in store.list_candles(inst.id, job.timeframe) if c.timestamp >= V3_RESEARCH_START]
        validated.append(validate_survivor(job=job, candles=candles))
    robust = [v for v in validated if v.get("robustness") == "ROBUST"]
    promising = [v for v in validated if v.get("robustness") in ("ROBUST", "PROMISING")]
    promising.sort(key=lambda r: (-((r.get("oos") or {}).get("expectancy_r") or -999),))
    portfolio = promising[:6]
    sim = portfolio_monte_carlo_and_10k([r for r in portfolio if r.get("robustness") == "ROBUST"] or portfolio[:3])
    session.close()
    return {
        "stage_a_survivors": len(survivors),
        "stage_b_validated": len(validated),
        "robust_candidates": robust,
        "top10": promising[:10],
        "production_portfolio": portfolio[:6],
        **sim,
        "robust_edge_found": len(robust) > 0,
    }


def main() -> None:
    session = sessionmaker(bind=create_engine(db_url()))()
    total = len(plan_v31_jobs(TradingStore(session)))
    session.close()
    log(f"v3.1 watch total_jobs={total}")
    wait_for_checkpoint_complete(checkpoint_path=CHECKPOINT, total=total, poll_seconds=45, log=log)
    write_authoritative_progress(
        progress_path=PROGRESS,
        checkpoint_path=CHECKPOINT,
        total=total,
        registry_path=REGISTRY,
    )
    outcome = run_stage_b()
    family_status = {}
    fs = RESEARCH / "v3_1_family_status.json"
    if fs.is_file():
        family_status = json.loads(fs.read_text(encoding="utf-8"))
    failed_families = [k for k, v in family_status.items() if v.get("failed")]
    outcome["v3_1_total_jobs"] = total
    outcome["v3_1_completed"] = unique_completed_count(CHECKPOINT)
    outcome["failed_families"] = failed_families
    outcome["families_tested"] = list(family_status.keys())
    if not outcome["robust_edge_found"]:
        outcome["v32_plan"] = {
            "note": "V3.1 ROBUST=0 — next: session-specific crypto microstructure, carry FX, gold London fix breakout",
            "families_not_tested": ["carry_momentum_fx", "crypto_funding_proxy", "gold_london_break"],
        }
        PLAN.write_text(json.dumps(outcome["v32_plan"], indent=2), encoding="utf-8")
    OUTCOME.write_text(json.dumps(outcome, indent=2, default=str), encoding="utf-8")
    log(f"done outcome={OUTCOME} robust={len(outcome.get('robust_candidates') or [])}")


if __name__ == "__main__":
    main()
