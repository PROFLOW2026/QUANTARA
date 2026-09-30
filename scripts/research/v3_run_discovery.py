#!/usr/bin/env python3
"""Run full V3 edge discovery (read-only DB)."""

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
from quantara_engine.research.v3.candidates import export_frozen_catalog
from quantara_engine.research.v3.discovery import run_v3_discovery


def db_url() -> str:
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("DATABASE_URL="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise RuntimeError("DATABASE_URL missing")


def main() -> None:
    session = sessionmaker(bind=create_engine(db_url()))()
    store = TradingStore(session)
    if os.environ.get("V3_CATALOG_ONLY") == "1":
        print(json.dumps({"frozen_catalog": export_frozen_catalog()}, indent=2))
        return
    import sys

    def _progress(msg: str) -> None:
        print(msg, file=sys.stderr, flush=True)

    report = run_v3_discovery(store, progress=_progress)
    session.close()
    out_path = ROOT / "scripts" / "research" / "v3_discovery_report.json"
    out_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    summary = {
        "gate": report.get("gate"),
        "candidates_defined": report.get("candidates_defined"),
        "candidates_tested": report.get("candidates_tested"),
        "robust": report.get("candidates_passing_robustness"),
        "promising": report.get("candidates_passing_promising"),
        "production_finalists": len(report.get("production_finalists") or []),
    }
    print(json.dumps(summary, indent=2))
    print(f"full_report={out_path}")


if __name__ == "__main__":
    main()
