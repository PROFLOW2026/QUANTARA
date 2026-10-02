#!/usr/bin/env python3
"""Rerun Stage B for all current Live Sim manifest rows; update classifications prospectively."""

from __future__ import annotations

import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps" / "engine"))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from quantara_engine.live_sim.v32_registry import (
    V32_QUALIFIED_MANIFEST,
    load_v32_live_sim_active_combinations,
    load_v32_qualified_combinations,
)
from quantara_engine.persistence.store import TradingStore
from quantara_engine.research.v3.constants import V3_RESEARCH_START
from quantara_engine.research.v3_2.job_plan import V32Job
from quantara_engine.research.v3_2.stage_b import validate_survivor_broker


def db_url() -> str:
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("DATABASE_URL="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise RuntimeError("DATABASE_URL missing")


def _norm_cls(raw: str | None) -> str:
    return str(raw or "FAIL").upper()


def _transition(old: str, new: str) -> str:
    if old == new and old == "PASS":
        return "UNCHANGED_PASS"
    if old == new and old == "WEAK_PASS":
        return "UNCHANGED_WEAK_PASS"
    if old == "PASS" and new == "WEAK_PASS":
        return "PASS->WEAK_PASS"
    if old == "PASS" and new == "FAIL":
        return "PASS->FAIL"
    if old == "WEAK_PASS" and new == "PASS":
        return "WEAK_PASS->PASS"
    if old == "WEAK_PASS" and new == "FAIL":
        return "WEAK_PASS->FAIL"
    if old == new:
        return f"UNCHANGED_{old}"
    return f"{old}->{new}"


def main() -> None:
    active = list(load_v32_live_sim_active_combinations())
    before_count = len(active)
    session = sessionmaker(bind=create_engine(db_url(), pool_pre_ping=True))()
    store = TradingStore(session)

    candle_cache: dict[tuple[str, str], list] = {}
    per_candidate: list[dict] = []
    transitions: Counter[str] = Counter()

    for idx, row in enumerate(active, 1):
        key = row["key"]
        print(f"[{idx}/{before_count}] {key}", flush=True)
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
        ck = (job.asset, job.timeframe)
        if ck not in candle_cache:
            inst0 = store.get_instrument_by_symbol(job.asset)
            candle_cache[ck] = store.list_candles(
                inst0.id, job.timeframe, since=V3_RESEARCH_START, limit=20000
            )
        inst = store.get_instrument_by_symbol(job.asset)
        candles = candle_cache[ck]
        rep = validate_survivor_broker(job=job, candles=candles, store=store, instrument=inst)
        br = rep.get("broker_replay") or {}
        old_cls = _norm_cls(row.get("classification"))
        new_cls = _norm_cls(br.get("classification"))
        tr = _transition(old_cls, new_cls)
        transitions[tr] += 1
        per_candidate.append(
            {
                "key": key,
                "asset": job.asset,
                "old_classification": old_cls,
                "new_classification": new_cls,
                "transition": tr,
                "trades": br.get("trades"),
                "pf": br.get("pf"),
                "expectancy_r": br.get("expectancy_r"),
                "max_dd_pct": br.get("max_dd_pct"),
                "fills": br.get("fills"),
                "broker_fills": br.get("fills"),
            }
        )

    session.close()

    # Update manifest rows for recalibrated keys only.
    by_key = {r["key"]: r for r in per_candidate}
    qualified = [dict(r) for r in load_v32_qualified_combinations()]
    for entry in qualified:
        upd = by_key.get(entry.get("key"))
        if upd:
            entry["classification"] = upd["new_classification"]

    manifest = {"version": "3.2", "qualified": qualified}
    V32_QUALIFIED_MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    load_v32_qualified_combinations.cache_clear()
    after_active = len(load_v32_live_sim_active_combinations())

    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "commit_note": "Stage-B isolation fix (research_replay_isolation, max_open_positions=1)",
        "before_active": before_count,
        "rerun_completed": len(per_candidate),
        "after_active_eligible": after_active,
        "transitions": dict(transitions),
        "candidates": per_candidate,
    }
    out_path = ROOT / "scripts/research/v32_live_stage_b_recalibrate.json"
    out_path.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in out.items() if k != "candidates"}, indent=2))


if __name__ == "__main__":
    main()
