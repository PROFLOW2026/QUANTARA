#!/usr/bin/env python3
"""V3.2 Stage B — sharded full-broker replays for Stage-A survivors."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
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
from quantara_engine.research.v3_2.stage_b import validate_survivor_broker

RESEARCH = ROOT / "scripts" / "research"
LOG_DIR = RESEARCH / "v3_2_logs"
CHECKPOINT = RESEARCH / "v3_2_stage_b_checkpoint.jsonl"
REGISTRY = RESEARCH / "v3_2_stage_b_jobs.json"
REG_LOCK = RESEARCH / ".v3_2_stage_b_jobs.lock"
PAUSE_FILE = RESEARCH / "v3_2_stage_b.paused"
STAGE_A = RESEARCH / "v3_2_discovery_checkpoint.jsonl"


def db_url() -> str:
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("DATABASE_URL="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise RuntimeError("DATABASE_URL missing")


def survivor_keys() -> list[str]:
    rows = load_completed(STAGE_A)
    return sorted(k for k, r in rows.items() if r.get("stage_a_pass"))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--shard-id", type=int, required=True)
    parser.add_argument("--num-shards", type=int, default=2)
    args = parser.parse_args()

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    out_fh = open(LOG_DIR / f"stage_b_shard{args.shard_id}.stdout.log", "a", encoding="utf-8")
    err_fh = open(LOG_DIR / f"stage_b_shard{args.shard_id}.stderr.log", "a", encoding="utf-8")
    pid = os.getpid()
    print(json.dumps({"event": "stage_b_start", "shard": args.shard_id, "pid": pid}), flush=True)

    if PAUSE_FILE.is_file():
        print(json.dumps({"event": "paused", "file": str(PAUSE_FILE)}), flush=True)
        return

    session = sessionmaker(bind=create_engine(db_url(), pool_pre_ping=True))()
    store = TradingStore(session)
    job_map = {j.key: j for j in plan_v32_jobs(store)}
    keys = survivor_keys()
    init_registry_jobs(
        REGISTRY,
        REG_LOCK,
        [{"key": k, "candidate_id": k, "asset": job_map[k].asset, "timeframe": job_map[k].timeframe} for k in keys if k in job_map],
    )
    completed = load_completed(CHECKPOINT)

    for i, key in enumerate(keys):
        if i % args.num_shards != args.shard_id:
            continue
        if key in completed:
            finish_job(registry_path=REGISTRY, lock_path=REG_LOCK, job_key=key, pid=pid, status="DONE")
            continue
        if not claim_job(registry_path=REGISTRY, lock_path=REG_LOCK, job_key=key, pid=pid):
            continue
        job = job_map.get(key)
        if not job:
            finish_job(registry_path=REGISTRY, lock_path=REG_LOCK, job_key=key, pid=pid, status="FAILED")
            continue
        t0 = time.time()
        print(json.dumps({"event": "stage_b_job", "key": key}), flush=True)
        try:
            inst = store.get_instrument_by_symbol(job.asset)
            candles = [c for c in store.list_candles(inst.id, job.timeframe) if c.timestamp >= V3_RESEARCH_START]
            row = validate_survivor_broker(job=job, candles=candles, store=store, instrument=inst)
            with registry_lock(REG_LOCK):
                append_result(CHECKPOINT, row)
            finish_job(registry_path=REGISTRY, lock_path=REG_LOCK, job_key=key, pid=pid, status="DONE")
            session.rollback()
        except Exception:
            err_fh.write(traceback.format_exc() + "\n")
            finish_job(registry_path=REGISTRY, lock_path=REG_LOCK, job_key=key, pid=pid, status="FAILED")
            session.rollback()
        print(json.dumps({"event": "stage_b_done", "key": key, "sec": round(time.time() - t0, 2)}), flush=True)

    session.close()
    print(json.dumps({"shard": args.shard_id, "completed": len(load_completed(CHECKPOINT)), "total": len(keys)}), flush=True)


if __name__ == "__main__":
    main()
