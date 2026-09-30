#!/usr/bin/env python3
"""Mark checkpoint rows DONE in job registry (one-time / resume helper)."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps" / "engine"))

from quantara_engine.research.v3.checkpoint import load_completed
from quantara_engine.research.v3.job_registry import finish_job, init_registry_jobs, load_registry, registry_lock, save_registry

RESEARCH = ROOT / "scripts" / "research"
CHECKPOINT = RESEARCH / "v3_discovery_checkpoint.jsonl"
REGISTRY = RESEARCH / "v3_discovery_jobs.json"
REG_LOCK = RESEARCH / ".v3_discovery_jobs.lock"


def main() -> None:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from quantara_engine.persistence.store import TradingStore
    from quantara_engine.research.v3.job_plan import plan_v3_jobs

    url = [l.split("=", 1)[1].strip().strip('"').strip("'") for l in (ROOT / ".env").read_text().splitlines() if l.startswith("DATABASE_URL=")][0]
    s = sessionmaker(bind=create_engine(url))()
    jobs = plan_v3_jobs(TradingStore(s))
    s.close()
    init_registry_jobs(
        REGISTRY,
        REG_LOCK,
        [{"key": j.key, "candidate_id": j.candidate_id, "asset": j.asset, "timeframe": j.timeframe} for j in jobs],
    )
    done = load_completed(CHECKPOINT)
    with registry_lock(REG_LOCK):
        reg = load_registry(REGISTRY)
        for key in done:
            reg.setdefault("jobs", {})[key] = {
                **reg["jobs"].get(key, {}),
                "status": "DONE",
            }
        save_registry(REGISTRY, reg)
    print(json.dumps({"synced_done": len(done), "total": len(jobs)}))


if __name__ == "__main__":
    main()
