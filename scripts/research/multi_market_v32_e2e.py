#!/usr/bin/env python3
"""Stage A + Stage B for multi-market symbols (crypto, gold, FX) — no Live Sim reset."""

from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps" / "engine"))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

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
REGISTRY_A = RESEARCH / "multi_market_v3_2_discovery_jobs.json"
REG_LOCK_A = RESEARCH / ".multi_market_v3_2_discovery_jobs.lock"
REGISTRY_B = RESEARCH / "multi_market_v3_2_stage_b_jobs.json"
REG_LOCK_B = RESEARCH / ".multi_market_v3_2_stage_b_jobs.lock"

MULTI_MARKET = frozenset(
    {
        "BTCUSD",
        "ETHUSD",
        "XAUUSD",
        "GBPJPY",
        "SOLUSD",
        "XRPUSD",
        "EURUSD",
        "USDJPY",
        "GBPUSD",
        "AUDUSD",
    }
)


def db_url() -> str:
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("DATABASE_URL="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise RuntimeError("DATABASE_URL missing")


def main() -> None:
    session = sessionmaker(bind=create_engine(db_url(), pool_pre_ping=True))()
    store = TradingStore(session)
    jobs = [j for j in plan_v32_jobs(store) if j.asset in MULTI_MARKET]
    if not jobs:
        print({"status": "no_jobs_planned", "symbols": sorted(MULTI_MARKET)})
        return

    init_registry_jobs(
        REGISTRY_A,
        REG_LOCK_A,
        [{"key": j.key, "candidate_id": j.key, "asset": j.asset, "timeframe": j.timeframe} for j in jobs],
    )
    completed_a = load_completed(CHECKPOINT_A)
    pid = os.getpid()
    cache: dict[tuple[str, str], list] = {}
    stage_a_new = 0
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
            stage_b_new += 1
            finish_job(registry_path=REGISTRY_B, lock_path=REG_LOCK_B, job_key=job.key, pid=pid, status="DONE")
            session.rollback()
        except Exception:
            traceback.print_exc()
            finish_job(registry_path=REGISTRY_B, lock_path=REG_LOCK_B, job_key=job.key, pid=pid, status="FAILED")
            session.rollback()

    session.close()
    print(
        {
            "jobs_planned": len(jobs),
            "stage_a_new": stage_a_new,
            "survivors": len(survivors),
            "stage_b_new": stage_b_new,
        }
    )


if __name__ == "__main__":
    main()
