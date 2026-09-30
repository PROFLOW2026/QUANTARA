#!/usr/bin/env python3
"""Sharded V3 discovery worker — local only, shares checkpoint + registry."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps" / "engine"))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from quantara_engine.persistence.store import TradingStore
from quantara_engine.research.v3.candidates import frozen_v3_candidates
from quantara_engine.research.v3.checkpoint import append_result, load_completed
from quantara_engine.research.v3.constants import V3_RESEARCH_START
from quantara_engine.research.v3.discovery import run_v3_job
from quantara_engine.research.v3.job_plan import plan_v3_jobs
from quantara_engine.research.v3.job_registry import claim_job, finish_job, init_registry_jobs

RESEARCH = ROOT / "scripts" / "research"
CHECKPOINT = RESEARCH / "v3_discovery_checkpoint.jsonl"
REGISTRY = RESEARCH / "v3_discovery_jobs.json"
REG_LOCK = RESEARCH / ".v3_discovery_jobs.lock"
PROGRESS = RESEARCH / "v3_discovery_progress.json"


def db_url() -> str:
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("DATABASE_URL="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise RuntimeError("DATABASE_URL missing")


def candidate_by_id(cid: str):
    for c in frozen_v3_candidates():
        if c.candidate_id == cid:
            return c
    return None


def update_progress(*, total: int, done_keys: set[str], registry_path: Path) -> None:
    reg = json.loads(registry_path.read_text(encoding="utf-8")) if registry_path.is_file() else {"jobs": {}}
    jobs = reg.get("jobs") or {}
    running = sum(1 for j in jobs.values() if j.get("status") == "RUNNING")
    failed = sum(1 for j in jobs.values() if j.get("status") == "FAILED")
    completed = len(done_keys)
    remaining = max(0, total - completed)
    PROGRESS.write_text(
        json.dumps(
            {
                "total": total,
                "completed": completed,
                "running": running,
                "failed": failed,
                "remaining": remaining,
                "updated_at": time.time(),
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--shard-id", type=int, required=True)
    parser.add_argument("--num-shards", type=int, default=2)
    args = parser.parse_args()
    pid = os.getpid()

    engine = create_engine(db_url(), pool_pre_ping=True)
    session = sessionmaker(bind=engine)()
    store = TradingStore(session)

    jobs = plan_v3_jobs(store)
    init_registry_jobs(
        REGISTRY,
        REG_LOCK,
        [
            {
                "key": j.key,
                "candidate_id": j.candidate_id,
                "asset": j.asset,
                "timeframe": j.timeframe,
            }
            for j in jobs
        ],
    )
    completed = load_completed(CHECKPOINT)
    candle_cache: dict[tuple[str, str], list] = {}

    for job in jobs:
        if job.index % args.num_shards != args.shard_id:
            continue
        if job.key in completed:
            finish_job(
                registry_path=REGISTRY,
                lock_path=REG_LOCK,
                job_key=job.key,
                pid=pid,
                status="DONE",
            )
            continue
        if not claim_job(
            registry_path=REGISTRY,
            lock_path=REG_LOCK,
            job_key=job.key,
            pid=pid,
        ):
            continue

        candidate = candidate_by_id(job.candidate_id)
        if not candidate:
            finish_job(
                registry_path=REGISTRY,
                lock_path=REG_LOCK,
                job_key=job.key,
                pid=pid,
                status="FAILED",
            )
            continue

        instrument = store.get_instrument_by_symbol(job.asset)
        if not instrument:
            finish_job(
                registry_path=REGISTRY,
                lock_path=REG_LOCK,
                job_key=job.key,
                pid=pid,
                status="FAILED",
            )
            continue

        cache_key = (str(instrument.id), job.timeframe)
        if cache_key not in candle_cache:
            candle_cache[cache_key] = [
                c
                for c in store.list_candles(instrument.id, job.timeframe)
                if c.timestamp >= V3_RESEARCH_START
            ]
        candles = candle_cache[cache_key]
        row = run_v3_job(
            store,
            candidate=candidate,
            symbol=job.asset,
            timeframe=job.timeframe,
            candles=candles,
        )
        if row is None:
            finish_job(
                registry_path=REGISTRY,
                lock_path=REG_LOCK,
                job_key=job.key,
                pid=pid,
                status="FAILED",
            )
        else:
            append_result(CHECKPOINT, row)
            completed[job.key] = row
            finish_job(
                registry_path=REGISTRY,
                lock_path=REG_LOCK,
                job_key=job.key,
                pid=pid,
                status="DONE",
            )
        update_progress(total=len(jobs), done_keys=set(completed.keys()), registry_path=REGISTRY)

    session.close()
    update_progress(total=len(jobs), done_keys=set(completed.keys()), registry_path=REGISTRY)
    print(json.dumps({"shard": args.shard_id, "completed": len(completed), "total": len(jobs)}))


if __name__ == "__main__":
    main()
