#!/usr/bin/env python3
"""Run full V3 edge discovery (read-only DB) with checkpoint + single-instance lock."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps" / "engine"))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from quantara_engine.persistence.store import TradingStore
from quantara_engine.research.v3.checkpoint import acquire_run_lock, release_run_lock
from quantara_engine.research.v3.candidates import export_frozen_catalog
from quantara_engine.research.v3.discovery import run_v3_discovery

RESEARCH_DIR = ROOT / "scripts" / "research"
CHECKPOINT = RESEARCH_DIR / "v3_discovery_checkpoint.jsonl"
STATE = RESEARCH_DIR / "v3_discovery_state.json"
LOCK = RESEARCH_DIR / ".v3_discovery.lock"
REPORT = RESEARCH_DIR / "v3_discovery_report.json"


def db_url() -> str:
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("DATABASE_URL="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise RuntimeError("DATABASE_URL missing")


def main() -> None:
    if os.environ.get("V3_CATALOG_ONLY") == "1":
        print(json.dumps({"frozen_catalog": export_frozen_catalog()}, indent=2))
        return

    if not acquire_run_lock(LOCK):
        print(
            json.dumps(
                {
                    "error": "another_v3_discovery_running",
                    "lock": str(LOCK),
                    "hint": "Wait for the active run or remove stale lock if no python process.",
                }
            ),
            file=sys.stderr,
        )
        sys.exit(2)

    session = sessionmaker(bind=create_engine(db_url()))()
    store = TradingStore(session)

    def _progress(msg: str) -> None:
        print(msg, file=sys.stderr, flush=True)

    try:
        report = run_v3_discovery(
            store,
            progress=_progress,
            checkpoint_path=CHECKPOINT,
            state_path=STATE,
        )
    finally:
        session.close()
        release_run_lock(LOCK)

    REPORT.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    summary = {
        "gate": report.get("gate"),
        "discovery_complete": report.get("discovery_complete"),
        "candidates_defined": report.get("candidates_defined"),
        "backtests_planned": report.get("backtests_planned"),
        "backtests_completed": report.get("backtests_completed"),
        "robustness_counts": report.get("robustness_counts"),
        "robust": report.get("candidates_passing_robustness"),
        "promising": report.get("candidates_passing_promising"),
        "production_finalists": len(report.get("production_finalists") or []),
    }
    print(json.dumps(summary, indent=2))
    print(f"full_report={REPORT}")


if __name__ == "__main__":
    main()
