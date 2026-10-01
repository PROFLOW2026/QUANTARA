#!/usr/bin/env python3
"""Phase 2 — Stage A + Stage B + manifest sync for gated symbols only."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps" / "engine"))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from quantara_engine.market_data.active_universe import PHASE2_EXPANDED_DB_SYMBOLS
from quantara_engine.persistence.store import TradingStore
from quantara_engine.research.v3.checkpoint import append_result, load_completed
from quantara_engine.research.v3.constants import V3_RESEARCH_START
from quantara_engine.research.v3.job_registry import claim_job, finish_job, init_registry_jobs, registry_lock
from quantara_engine.research.v3_2.job_plan import plan_v32_jobs
from quantara_engine.research.v3_2.run_job import run_v32_stage_a_job
from quantara_engine.research.v3_2.stage_b import validate_survivor_broker

RESEARCH = ROOT / "scripts" / "research"
CHECKPOINT_A = RESEARCH / "v3_2_discovery_checkpoint.jsonl"
CHECKPOINT_B = RESEARCH / "v3_2_stage_b_checkpoint.jsonl"
REGISTRY_A = RESEARCH / "phase2_v3_2_discovery_jobs.json"
REG_LOCK_A = RESEARCH / ".phase2_v3_2_discovery_jobs.lock"
REGISTRY_B = RESEARCH / "phase2_v3_2_stage_b_jobs.json"
REG_LOCK_B = RESEARCH / ".phase2_v3_2_stage_b_jobs.lock"
VERIFY = RESEARCH / "phase2_universe_verify.json"
OUT = RESEARCH / "phase2_e2e_summary.json"
PHASE2 = frozenset(PHASE2_EXPANDED_DB_SYMBOLS)


def db_url() -> str:
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("DATABASE_URL="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise RuntimeError("DATABASE_URL missing")


def _gated_symbols() -> set[str]:
    if VERIFY.is_file():
        data = json.loads(VERIFY.read_text(encoding="utf-8"))
        return set(data.get("passed") or [])
    return set(PHASE2)


def main() -> None:
    gated = _gated_symbols()
    if not gated:
        raise SystemExit("no Phase 2 symbols passed data gate — run phase2_verify_universe.py after bootstrap")

    session = sessionmaker(bind=create_engine(db_url(), pool_pre_ping=True))()
    store = TradingStore(session)
    jobs = [j for j in plan_v32_jobs(store) if j.asset in gated]
    init_registry_jobs(
        REGISTRY_A,
        REG_LOCK_A,
        [{"key": j.key, "candidate_id": j.key, "asset": j.asset, "timeframe": j.timeframe} for j in jobs],
    )
    completed_a = load_completed(CHECKPOINT_A)
    pid = os.getpid()
    stage_a_new = 0
    cache: dict[tuple[str, str], list] = {}

    for job in jobs:
        if job.key in completed_a:
            continue
        if not claim_job(registry_path=REGISTRY_A, lock_path=REG_LOCK_A, job_key=job.key, pid=pid):
            continue
        inst = store.get_instrument_by_symbol(job.asset)
        if not inst:
            finish_job(registry_path=REGISTRY_A, lock_path=REG_LOCK_A, job_key=job.key, pid=pid, status="FAILED")
            continue
        ck = (str(inst.id), job.timeframe)
        if ck not in cache:
            cache[ck] = [c for c in store.list_candles(inst.id, job.timeframe) if c.timestamp >= V3_RESEARCH_START]
        if len(cache[ck]) < 250:
            finish_job(registry_path=REGISTRY_A, lock_path=REG_LOCK_A, job_key=job.key, pid=pid, status="FAILED")
            continue
        try:
            row = run_v32_stage_a_job(job=job, candles=cache[ck], store=store, instrument=inst)
            if row is None:
                finish_job(registry_path=REGISTRY_A, lock_path=REG_LOCK_A, job_key=job.key, pid=pid, status="FAILED")
                continue
            with registry_lock(REG_LOCK_A):
                append_result(CHECKPOINT_A, row)
            stage_a_new += 1
            finish_job(registry_path=REGISTRY_A, lock_path=REG_LOCK_A, job_key=job.key, pid=pid, status="DONE")
            session.rollback()
        except Exception:
            traceback.print_exc()
            finish_job(registry_path=REGISTRY_A, lock_path=REG_LOCK_A, job_key=job.key, pid=pid, status="FAILED")
            session.rollback()

    completed_a = load_completed(CHECKPOINT_A)
    survivors = [j for j in jobs if completed_a.get(j.key, {}).get("stage_a_pass")]
    init_registry_jobs(
        REGISTRY_B,
        REG_LOCK_B,
        [{"key": j.key, "candidate_id": j.key, "asset": j.asset, "timeframe": j.timeframe} for j in survivors],
    )
    completed_b = load_completed(CHECKPOINT_B)
    stage_b_new = 0
    for job in survivors:
        if job.key in completed_b:
            continue
        if not claim_job(registry_path=REGISTRY_B, lock_path=REG_LOCK_B, job_key=job.key, pid=pid):
            continue
        inst = store.get_instrument_by_symbol(job.asset)
        ck = (str(inst.id), job.timeframe)
        if ck not in cache:
            cache[ck] = [c for c in store.list_candles(inst.id, job.timeframe) if c.timestamp >= V3_RESEARCH_START]
        try:
            row = validate_survivor_broker(job=job, candles=cache[ck], store=store, instrument=inst)
            with registry_lock(REG_LOCK_B):
                append_result(CHECKPOINT_B, row)
            finish_job(registry_path=REGISTRY_B, lock_path=REG_LOCK_B, job_key=job.key, pid=pid, status="DONE")
            stage_b_new += 1
            session.rollback()
        except Exception:
            traceback.print_exc()
            finish_job(registry_path=REGISTRY_B, lock_path=REG_LOCK_B, job_key=job.key, pid=pid, status="FAILED")
            session.rollback()

    session.close()

    subprocess.run([sys.executable, str(RESEARCH / "phase1_promote_and_report.py")], check=False, cwd=str(ROOT))
    subprocess.run([sys.executable, str(ROOT / "scripts" / "sync_v32_live_sim_candidates.py")], check=False, cwd=str(ROOT))

    summary = {
        "gated_symbols": sorted(gated),
        "stage_a_jobs": len(jobs),
        "stage_a_new": stage_a_new,
        "stage_b_survivors": len(survivors),
        "stage_b_new": stage_b_new,
        "timestamp": time.time(),
    }
    OUT.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
