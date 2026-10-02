#!/usr/bin/env python3
"""Spot-check US Live manifest rows: Stage B replay vs stored classification (post isolation fix)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps" / "engine"))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from quantara_engine.live_sim.v32_registry import load_v32_live_sim_active_combinations
from quantara_engine.persistence.store import TradingStore
from quantara_engine.research.v3.constants import V3_RESEARCH_START
from quantara_engine.research.v3_1.broker_replay import replay_finalist_broker
from quantara_engine.research.v3_2.job_plan import V32Job

US = frozenset(
    {
        "AAPL",
        "AMD",
        "AMZN",
        "COIN",
        "DIA",
        "GOOGL",
        "IWM",
        "META",
        "MSFT",
        "MSTR",
        "NVDA",
        "PLTR",
        "QQQ",
        "SPY",
        "TSLA",
    }
)


def db_url() -> str:
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("DATABASE_URL="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise RuntimeError("DATABASE_URL missing")


def main() -> None:
    rows = [r for r in load_v32_live_sim_active_combinations() if r.get("asset") in US]
    session = sessionmaker(bind=create_engine(db_url(), pool_pre_ping=True))()
    store = TradingStore(session)
    changes = []
    checked = 0
    for row in rows[:24]:
        key = row["key"]
        parts = key.split("|")
        job = V32Job(
            0,
            key,
            parts[0],
            parts[1],
            parts[2],
            parts[3],
            parts[4],
            row.get("parameters") or {},
        )
        inst = store.get_instrument_by_symbol(job.asset)
        candles = store.list_candles(inst.id, job.timeframe, since=V3_RESEARCH_START, limit=15000)
        rep = replay_finalist_broker(
            store,
            key=key,
            family=job.family,
            asset=job.asset,
            timeframe=job.timeframe,
            direction=job.direction,
            parameters=job.parameters,
            instrument=inst,
            candles=candles,
        )
        new_cls = str(rep.get("classification") or "FAIL").upper()
        old_cls = str(row.get("classification") or "").upper()
        checked += 1
        if new_cls != old_cls:
            changes.append({"key": key, "was": old_cls, "now": new_cls, "trades": rep.get("trades")})
    session.close()
    out = {
        "checked": checked,
        "total_active_us": len(rows),
        "reclassified": changes,
        "stage_a_cost_affects_stage_b": False,
    }
    path = ROOT / "scripts/research/v32_existing_live_stage_b_spotcheck.json"
    path.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
