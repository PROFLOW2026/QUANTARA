#!/usr/bin/env python3
"""Build v3_discovery_report.json from checkpoint when matrix complete."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps" / "engine"))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from quantara_engine.persistence.store import TradingStore
from quantara_engine.research.v3.checkpoint import load_completed
from quantara_engine.research.v3.discovery import run_v3_discovery
from quantara_engine.research.v3.job_plan import plan_v3_jobs

RESEARCH = ROOT / "scripts" / "research"
CHECKPOINT = RESEARCH / "v3_discovery_checkpoint.jsonl"
REPORT = RESEARCH / "v3_discovery_report.json"


def db_url() -> str:
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("DATABASE_URL="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise RuntimeError("DATABASE_URL missing")


def main() -> None:
    session = sessionmaker(bind=create_engine(db_url()))()
    store = TradingStore(session)
    total = len(plan_v3_jobs(store))
    done = load_completed(CHECKPOINT)
    session.close()
    if len(done) < total:
        print(json.dumps({"error": "incomplete", "completed": len(done), "total": total}))
        sys.exit(1)
    session = sessionmaker(bind=create_engine(db_url()))()
    store = TradingStore(session)
    report = run_v3_discovery(store, checkpoint_path=CHECKPOINT)
    session.close()
    REPORT.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(json.dumps({"gate": report.get("gate"), "path": str(REPORT)}, indent=2))


if __name__ == "__main__":
    main()
