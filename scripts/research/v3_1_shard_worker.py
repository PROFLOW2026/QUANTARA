#!/usr/bin/env python3
"""V3.1 sharded Stage-A worker with per-shard stdout/stderr logs."""

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
from quantara_engine.research.v3.job_registry import claim_job, finish_job, init_registry_jobs
from quantara_engine.research.v3.orchestration import write_authoritative_progress
from quantara_engine.research.v3_1.job_plan import plan_v31_jobs
from quantara_engine.research.v3_1.run_job import run_v31_stage_a_job
from quantara_engine.research.v3_1.screen import family_kill_update

RESEARCH = ROOT / "scripts" / "research"
LOG_DIR = RESEARCH / "v3_1_logs"
CHECKPOINT = RESEARCH / "v3_1_discovery_checkpoint.jsonl"
REGISTRY = RESEARCH / "v3_1_discovery_jobs.json"
REG_LOCK = RESEARCH / ".v3_1_discovery_jobs.lock"
PROGRESS = RESEARCH / "v3_1_discovery_progress.json"
FAMILY_STATUS = RESEARCH / "v3_1_family_status.json"
JOB_TIMEOUT_SEC = 900


class _Tee:
    def __init__(self, *streams):
        self._streams = streams

    def write(self, data: str) -> int:
        for s in self._streams:
            s.write(data)
            s.flush()
        return len(data)

    def flush(self) -> None:
        for s in self._streams:
            s.flush()


def db_url() -> str:
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("DATABASE_URL="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise RuntimeError("DATABASE_URL missing")


def _load_failed_families() -> set[str]:
    if not FAMILY_STATUS.is_file():
        return set()
    data = json.loads(FAMILY_STATUS.read_text(encoding="utf-8"))
    return {k for k, v in data.items() if v.get("failed")}


def _update_family_status(completed_rows: dict[str, dict]) -> None:
    by_family: dict[str, list] = {}
    for row in completed_rows.values():
        by_family.setdefault(row["family"], []).append(row.get("screen") or row)
    status = {}
    for fam, rows in by_family.items():
        status[fam] = family_kill_update(rows)
    FAMILY_STATUS.write_text(json.dumps(status, indent=2), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--shard-id", type=int, required=True)
    parser.add_argument("--num-shards", type=int, default=2)
    args = parser.parse_args()

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    out_fh = open(LOG_DIR / f"shard{args.shard_id}.stdout.log", "a", encoding="utf-8")
    err_fh = open(LOG_DIR / f"shard{args.shard_id}.stderr.log", "a", encoding="utf-8")
    sys.stdout = _Tee(sys.__stdout__, out_fh)
    sys.stderr = _Tee(sys.__stderr__, err_fh)

    pid = os.getpid()
    print(json.dumps({"event": "start", "shard": args.shard_id, "pid": pid}), flush=True)

    engine = create_engine(db_url(), pool_pre_ping=True)
    session = sessionmaker(bind=engine)()
    store = TradingStore(session)
    jobs = plan_v31_jobs(store)
    init_registry_jobs(
        REGISTRY,
        REG_LOCK,
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
    completed = load_completed(CHECKPOINT)
    candle_cache: dict[tuple[str, str], list] = {}
    failed_families = _load_failed_families()

    for job in jobs:
        if job.index % args.num_shards != args.shard_id:
            continue
        if job.family in failed_families:
            continue
        if job.key in completed:
            finish_job(registry_path=REGISTRY, lock_path=REG_LOCK, job_key=job.key, pid=pid, status="DONE")
            continue
        if not claim_job(registry_path=REGISTRY, lock_path=REG_LOCK, job_key=job.key, pid=pid):
            continue

        t0 = time.time()
        print(json.dumps({"event": "job_start", "key": job.key}), flush=True)
        try:
            instrument = store.get_instrument_by_symbol(job.asset)
            if not instrument:
                raise RuntimeError("missing instrument")
            cache_key = (str(instrument.id), job.timeframe)
            if cache_key not in candle_cache:
                candle_cache[cache_key] = [
                    c
                    for c in store.list_candles(instrument.id, job.timeframe)
                    if c.timestamp >= V3_RESEARCH_START
                ]
            candles = candle_cache[cache_key]
            if time.time() - t0 > JOB_TIMEOUT_SEC:
                raise TimeoutError("job setup timeout")
            row = run_v31_stage_a_job(job=job, candles=candles)
            if row is None:
                finish_job(registry_path=REGISTRY, lock_path=REG_LOCK, job_key=job.key, pid=pid, status="FAILED")
            else:
                row["key"] = job.key
                append_result(CHECKPOINT, row)
                completed[job.key] = row
                finish_job(registry_path=REGISTRY, lock_path=REG_LOCK, job_key=job.key, pid=pid, status="DONE")
        except Exception as exc:
            err_fh.write(traceback.format_exc() + "\n")
            err_fh.flush()
            fail_row = {
                "key": job.key,
                "family": job.family,
                "asset": job.asset,
                "timeframe": job.timeframe,
                "direction": job.direction,
                "error": str(exc),
                "stage_a_pass": False,
                "screen": {"stage_a": "FAIL", "reject_reasons": ["exception"]},
            }
            append_result(CHECKPOINT, fail_row)
            completed[job.key] = fail_row
            finish_job(registry_path=REGISTRY, lock_path=REG_LOCK, job_key=job.key, pid=pid, status="FAILED")
        elapsed = time.time() - t0
        print(json.dumps({"event": "job_done", "key": job.key, "elapsed_sec": round(elapsed, 2)}), flush=True)

        completed = load_completed(CHECKPOINT)
        write_authoritative_progress(
            progress_path=PROGRESS,
            checkpoint_path=CHECKPOINT,
            total=len(jobs),
            registry_path=REGISTRY,
        )
        _update_family_status(completed)
        failed_families = _load_failed_families()

    session.close()
    completed = load_completed(CHECKPOINT)
    write_authoritative_progress(
        progress_path=PROGRESS,
        checkpoint_path=CHECKPOINT,
        total=len(jobs),
        registry_path=REGISTRY,
    )
    print(json.dumps({"shard": args.shard_id, "completed_unique": len(completed), "total": len(jobs)}), flush=True)


if __name__ == "__main__":
    main()
