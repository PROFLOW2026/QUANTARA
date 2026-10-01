#!/usr/bin/env python3
"""Mark family-killed / registry-DONE jobs in checkpoint so Stage A can finalize."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps" / "engine"))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from quantara_engine.persistence.store import TradingStore
from quantara_engine.research.v3.checkpoint import append_result, load_completed
from quantara_engine.research.v3.orchestration import write_authoritative_progress
from quantara_engine.research.v3_1.job_plan import plan_v31_jobs

RESEARCH = ROOT / "scripts" / "research"
CHECKPOINT = RESEARCH / "v3_1_discovery_checkpoint.jsonl"
REGISTRY = RESEARCH / "v3_1_discovery_jobs.json"
PROGRESS = RESEARCH / "v3_1_discovery_progress.json"
FAMILY_STATUS = RESEARCH / "v3_1_family_status.json"


def db_url() -> str:
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("DATABASE_URL="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise RuntimeError("DATABASE_URL missing")


def main() -> None:
    session = sessionmaker(bind=create_engine(db_url()))()
    jobs = plan_v31_jobs(TradingStore(session))
    session.close()
    done = load_completed(CHECKPOINT)
    failed_families = set()
    if FAMILY_STATUS.is_file():
        st = json.loads(FAMILY_STATUS.read_text(encoding="utf-8"))
        failed_families = {k for k, v in st.items() if v.get("failed")}
    added = 0
    for job in jobs:
        if job.key in done:
            continue
        if job.family not in failed_families:
            continue
        row = {
            "key": job.key,
            "candidate_id": f"{job.family}_{job.variant_id}",
            "family": job.family,
            "variant_id": job.variant_id,
            "asset": job.asset,
            "timeframe": job.timeframe,
            "direction": job.direction,
            "parameters": job.parameters,
            "stage_a_pass": False,
            "skip_reason": "family_failed_early_kill",
            "screen": {"stage_a": "FAIL", "reject_reasons": ["family_killed"]},
        }
        append_result(CHECKPOINT, row)
        done[job.key] = row
        added += 1
    write_authoritative_progress(
        progress_path=PROGRESS,
        checkpoint_path=CHECKPOINT,
        total=len(jobs),
        registry_path=REGISTRY,
    )
    print(json.dumps({"added_skipped": added, "unique": len(done), "total": len(jobs)}, indent=2))


if __name__ == "__main__":
    main()
