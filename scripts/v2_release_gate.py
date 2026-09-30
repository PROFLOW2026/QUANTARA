#!/usr/bin/env python3
"""QUANTARA V2 release gate — read-only replay + integrity snapshot."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "engine"))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from quantara_engine.live_sim.offline_replay import robot_b_min_qty_replay, run_clean_window_replay
from quantara_engine.persistence.store import TradingStore

ANCHOR = datetime.fromisoformat("2026-09-20 06:55:47.052571+03:00").astimezone(timezone.utc)


def db_url() -> str:
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("DATABASE_URL="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise RuntimeError("DATABASE_URL missing")


def main() -> None:
    session = sessionmaker(bind=create_engine(db_url()))()
    store = TradingStore(session)
    replay = run_clean_window_replay(store, anchor=ANCHOR)
    robot_b = robot_b_min_qty_replay(store, anchor=ANCHOR)
    session.close()
    out = {
        "anchor_utc": ANCHOR.isoformat(),
        "replay": replay,
        "robot_b_min_qty": robot_b,
        "attribution": {
            "policy_improvement_pnl": round(
                (replay["v2"]["net_pnl"] or 0) - (replay["v2_sizing_only_with_v1_policy"]["net_pnl"] or 0),
                2,
            ),
            "sizing_improvement_pnl": round(
                (replay["v2_sizing_only_with_v1_policy"]["net_pnl"] or 0) - (replay["v1"]["net_pnl"] or 0),
                2,
            ),
        },
    }
    out_path = ROOT / "scripts" / "_tmp_v2_release_gate_out.json"
    out_path.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    print(json.dumps(out, indent=2, default=str))


if __name__ == "__main__":
    main()
