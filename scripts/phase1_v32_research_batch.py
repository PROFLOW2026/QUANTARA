#!/usr/bin/env python3
"""Run V3.2 Stage A (+ Stage B for survivors) for Phase 1 symbols only."""

from __future__ import annotations

import json
import os
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "engine"))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from quantara_engine.market_data.active_universe import PHASE1_EXPANDED_DB_SYMBOLS
from quantara_engine.persistence.store import TradingStore
from quantara_engine.research.v3.checkpoint import append_result, load_completed
from quantara_engine.research.v3.constants import V3_RESEARCH_START
from quantara_engine.research.v3.job_registry import claim_job, finish_job, init_registry_jobs, registry_lock
from quantara_engine.research.v3_2.job_plan import plan_v32_jobs
from quantara_engine.research.v3_2.run_job import run_v32_stage_a_job
RESEARCH = ROOT / "scripts" / "research"
CHECKPOINT_A = RESEARCH / "v3_2_discovery_checkpoint.jsonl"
CHECKPOINT_B = RESEARCH / "v3_2_stage_b_checkpoint.jsonl"
REGISTRY_A = RESEARCH / "v3_2_discovery_jobs.json"
REG_LOCK_A = RESEARCH / ".v3_2_discovery_jobs.lock"
PHASE1_ASSETS = frozenset(PHASE1_EXPANDED_DB_SYMBOLS)
SUMMARY_OUT = RESEARCH / "phase1_v32_research_summary.json"


def db_url() -> str:
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("DATABASE_URL="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise RuntimeError("DATABASE_URL missing")


def _job_asset(key: str, row: dict) -> str | None:
    asset = row.get("asset")
    if asset:
        return str(asset).upper()
    parts = key.split("|")
    return parts[2].upper() if len(parts) > 2 else None


def _classify_phase1(rows: dict[str, dict]) -> dict[str, int]:
    out = {"PASS": 0, "WEAK_PASS": 0, "FAIL": 0}
    for key, row in rows.items():
        asset = _job_asset(key, row)
        if asset not in PHASE1_ASSETS:
            continue
        cls = str(row.get("classification") or "").upper()
        if cls in out:
            out[cls] += 1
    return out


def main() -> None:
    engine = create_engine(db_url(), pool_pre_ping=True)
    session = sessionmaker(bind=engine)()
    store = TradingStore(session)
    jobs = plan_v32_jobs(store)
    phase1_jobs = [j for j in jobs if j.asset in PHASE1_ASSETS]
    init_registry_jobs(
        REGISTRY_A,
        REG_LOCK_A,
        [
            {
                "key": j.key,
                "candidate_id": f"{j.family}:{j.variant_id}",
                "asset": j.asset,
                "timeframe": j.timeframe,
            }
            for j in jobs
        ],
    )

    completed_a_before = load_completed(CHECKPOINT_A)
    completed_b_before = load_completed(CHECKPOINT_B)
    pid = os.getpid()
    candle_cache: dict[tuple[str, str], list] = {}
    stage_a_new = 0
    stage_b_new = 0

    for job in phase1_jobs:
        if job.key in completed_a_before:
            continue
        if not claim_job(registry_path=REGISTRY_A, lock_path=REG_LOCK_A, job_key=job.key, pid=pid):
            continue
        instrument = store.get_instrument_by_symbol(job.asset)
        if not instrument:
            finish_job(registry_path=REGISTRY_A, lock_path=REG_LOCK_A, job_key=job.key, pid=pid, status="FAILED")
            continue
        cache_key = (str(instrument.id), job.timeframe)
        if cache_key not in candle_cache:
            candle_cache[cache_key] = [
                c for c in store.list_candles(instrument.id, job.timeframe) if c.timestamp >= V3_RESEARCH_START
            ]
        if len(candle_cache[cache_key]) < 250:
            finish_job(registry_path=REGISTRY_A, lock_path=REG_LOCK_A, job_key=job.key, pid=pid, status="FAILED")
            continue
        try:
            row = run_v32_stage_a_job(
                job=job, candles=candle_cache[cache_key], store=store, instrument=instrument
            )
            if row is None:
                finish_job(registry_path=REGISTRY_A, lock_path=REG_LOCK_A, job_key=job.key, pid=pid, status="FAILED")
                continue
            with registry_lock(REG_LOCK_A):
                append_result(CHECKPOINT_A, row)
            stage_a_new += 1
            finish_job(registry_path=REGISTRY_A, lock_path=REG_LOCK_A, job_key=job.key, pid=pid, status="DONE")
        except Exception:
            traceback.print_exc()
            finish_job(registry_path=REGISTRY_A, lock_path=REG_LOCK_A, job_key=job.key, pid=pid, status="FAILED")

    completed_a = load_completed(CHECKPOINT_A)
    summary = {
        "phase1_assets": sorted(PHASE1_ASSETS),
        "stage_a_jobs_planned_phase1": len(phase1_jobs),
        "stage_a_new_runs": stage_a_new,
        "stage_b_note": "Use scripts/research/v3_2_stage_b_shard_worker.py after Stage A survivors.",
        "timestamp": time.time(),
    }
    SUMMARY_OUT.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    session.close()


if __name__ == "__main__":
    main()
