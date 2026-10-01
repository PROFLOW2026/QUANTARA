#!/usr/bin/env python3
"""Phase 2 — master scoreboard + leaderboard + concentration (read-only)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "engine"))

from quantara_engine.db.session import session_scope
from quantara_engine.live_sim.discovery_observability import build_discovery_observability_report
from quantara_engine.live_sim.v32_registry import load_v32_live_sim_active_combinations
from quantara_engine.persistence.store import TradingStore

OUT = ROOT / "scripts" / "research" / "phase2_discovery_report.json"


def main() -> None:
    with session_scope() as session:
        store = TradingStore(session)
        report = build_discovery_observability_report(store)
        report["active_live_assets"] = len(
            {r["asset"] for r in load_v32_live_sim_active_combinations()}
        )
    OUT.write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, default=str))


if __name__ == "__main__":
    main()
