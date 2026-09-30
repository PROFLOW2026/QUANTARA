#!/usr/bin/env python3
"""V3.1 post-discovery — same shape as v3_final_outcome."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RESEARCH = ROOT / "scripts" / "research"
REPORT = RESEARCH / "v3_1_discovery_report.json"
OUT = RESEARCH / "v3_1_final_outcome.json"


def _load_post_module():
    path = RESEARCH / "v3_post_discovery_pipeline.py"
    spec = importlib.util.spec_from_file_location("v3_post_discovery_pipeline", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(mod)
    return mod


def main() -> None:
    if not REPORT.is_file():
        sys.exit(1)
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    adapted = {
        "top10": report.get("top10") or [],
        "robustness_counts": report.get("robustness_counts") or {},
        "monte_carlo_best": {},
    }
    sys.path.insert(0, str(ROOT / "apps" / "engine"))
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from quantara_engine.persistence.store import TradingStore
    from quantara_engine.research.v3.candidates_v31 import frozen_v31_candidates

    post = _load_post_module()
    session = sessionmaker(bind=create_engine(post.db_url()))()
    store = TradingStore(session)
    outcome = post.run_pipeline(store, adapted, candidates=frozen_v31_candidates())
    outcome["v31"] = True
    session.close()
    OUT.write_text(json.dumps(outcome, indent=2, default=str), encoding="utf-8")
    print(json.dumps({"ok": True, "path": str(OUT)}, indent=2))


if __name__ == "__main__":
    main()
