#!/usr/bin/env python3
"""V3.2: Stage A wait → Stage B checkpoint wait → outcome JSON."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps" / "engine"))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from quantara_engine.persistence.store import TradingStore
from quantara_engine.research.v3.checkpoint import load_completed
from quantara_engine.research.v3.orchestration import (
    unique_completed_count,
    wait_for_checkpoint_complete,
    write_authoritative_progress,
)
from quantara_engine.research.v3_2.job_plan import plan_v32_jobs
from quantara_engine.research.v3_2.stage_b import portfolio_monte_carlo_and_10k

RESEARCH = ROOT / "scripts" / "research"
CHECKPOINT = RESEARCH / "v3_2_discovery_checkpoint.jsonl"
STAGE_B = RESEARCH / "v3_2_stage_b_checkpoint.jsonl"
REGISTRY = RESEARCH / "v3_2_discovery_jobs.json"
PROGRESS = RESEARCH / "v3_2_discovery_progress.json"
OUTCOME = RESEARCH / "v3_2_final_outcome.json"
PAUSE_FILE = RESEARCH / "v3_2_stage_b.paused"
LOG = RESEARCH / "v3_2_autonomous_watch.log"


def log(msg: str) -> None:
    line = f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {msg}\n"
    with LOG.open("a", encoding="utf-8") as f:
        f.write(line)
    print(line, end="")


def db_url() -> str:
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("DATABASE_URL="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise RuntimeError("DATABASE_URL missing")


def survivor_count() -> int:
    return sum(1 for r in load_completed(CHECKPOINT).values() if r.get("stage_a_pass"))


def main() -> None:
    eng = create_engine(db_url())
    session = sessionmaker(bind=eng)()
    jobs = plan_v32_jobs(TradingStore(session))
    total = len(jobs)
    families_n = len({j.family for j in jobs})
    session.close()
    log(f"V3.2 watch: {total} Stage-A jobs")
    wait_for_checkpoint_complete(checkpoint_path=CHECKPOINT, total=total, poll_seconds=30, log=log)
    write_authoritative_progress(
        progress_path=PROGRESS,
        checkpoint_path=CHECKPOINT,
        total=total,
        registry_path=REGISTRY,
    )
    n_surv = survivor_count()
    log(f"Stage A survivors: {n_surv}")

    if PAUSE_FILE.is_file():
        log(f"Stage B PAUSED ({PAUSE_FILE.name}) — start v3_2_stage_b_shard_worker after forensics")
        return

    log(f"Waiting Stage B checkpoint {n_surv} jobs")
    wait_for_checkpoint_complete(checkpoint_path=STAGE_B, total=n_surv, poll_seconds=45, log=log)
    validated = list(load_completed(STAGE_B).values())
    robust = [v for v in validated if v.get("robustness") == "ROBUST"]
    promising = [v for v in validated if v.get("robustness") in ("ROBUST", "PROMISING")]
    promising.sort(
        key=lambda r: (
            -((r.get("oos") or {}).get("expectancy_r") or -999),
            -((r.get("broker_replay") or {}).get("fills") or 0),
        )
    )
    portfolio_rows = robust or promising[:3]
    sim = portfolio_monte_carlo_and_10k(portfolio_rows)
    outcome = {
        "families_tested": families_n,
        "total_jobs": total,
        "stage_a_survivors": n_surv,
        "stage_b_candidates": len(validated),
        "robust_count": len(robust),
        "promising_count": max(0, len(promising) - len(robust)),
        "robust_candidates": robust,
        "top_candidates": promising[:6],
        "robust_edge_found": len(robust) >= 2,
        **sim,
    }
    OUTCOME.write_text(json.dumps(outcome, indent=2, default=str), encoding="utf-8")
    log(f"Done outcome={OUTCOME} robust={len(robust)}")


if __name__ == "__main__":
    main()
