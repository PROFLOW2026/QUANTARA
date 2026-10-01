#!/usr/bin/env python3
"""Regenerate v32 manifest from Stage B, sync metrics, Phase 1 final report."""

from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps" / "engine"))

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from quantara_engine.live_sim.v32_registry import (
    V32_QUALIFIED_MANIFEST,
    load_v32_live_sim_active_combinations,
    v32_row_live_sim_eligible,
)
from quantara_engine.market_data.active_universe import PHASE1_EXPANDED_DB_SYMBOLS
from quantara_engine.persistence.store import TradingStore
from quantara_engine.research.v3.checkpoint import load_completed
from quantara_engine.research.v3_2.job_plan import plan_v32_jobs

RESEARCH = ROOT / "scripts" / "research"
STAGE_A = RESEARCH / "v3_2_discovery_checkpoint.jsonl"
STAGE_B = RESEARCH / "v3_2_stage_b_checkpoint.jsonl"
PHASE1 = frozenset(PHASE1_EXPANDED_DB_SYMBOLS)
REPORT_OUT = RESEARCH / "phase1_live_candidates_report.json"


def db_url() -> str:
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("DATABASE_URL="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise RuntimeError("DATABASE_URL missing")


def _asset_from_row(key: str, row: dict) -> str:
    a = row.get("asset")
    if a:
        return str(a).upper()
    parts = key.split("|")
    return parts[2].upper() if len(parts) > 2 else "?"


def _classification(row: dict) -> str:
    br = row.get("broker_replay") or {}
    cls = br.get("classification") or row.get("classification")
    return str(cls or "FAIL").upper()


def _variant_id(key: str) -> str:
    parts = key.split("|")
    return parts[1] if len(parts) > 1 else "v1"


def regenerate_manifest(stage_b: dict[str, dict]) -> list[dict]:
    qualified: list[dict] = []
    for key in sorted(stage_b.keys()):
        row = stage_b[key]
        cls = _classification(row)
        br = row.get("broker_replay") or {}
        qualified.append(
            {
                "key": key,
                "family": row.get("family") or key.split("|")[0],
                "variant_id": _variant_id(key),
                "asset": _asset_from_row(key, row),
                "timeframe": row.get("timeframe") or key.split("|")[3],
                "direction": row.get("direction") or key.split("|")[4],
                "parameters": row.get("parameters") or {},
                "robustness": row.get("robustness") or "FAIL",
                "classification": cls,
            }
        )
    return qualified


def stage_a_breakdown(stage_a: dict[str, dict]) -> dict:
    survivors = [r for r in stage_a.values() if r.get("stage_a_pass")]
    by_symbol = Counter(_asset_from_row(r.get("key", ""), r) for r in survivors)
    by_family = Counter(r.get("family") or "?" for r in survivors)
    by_tf = Counter(r.get("timeframe") or "?" for r in survivors)
    by_dir = Counter(r.get("direction") or "?" for r in survivors)
    return {
        "by_symbol": dict(by_symbol),
        "by_family": dict(by_family),
        "by_timeframe": dict(by_tf),
        "by_direction": dict(by_dir),
    }


def fx_freshness(store: TradingStore) -> dict:
    out = {}
    for sym in ("EURUSD", "USDJPY"):
        inst = store.get_instrument_by_symbol(sym)
        if not inst:
            out[sym] = {"latest_5m": None, "live_fresh": False}
            continue
        row = store.session.execute(
            text(
                """
                SELECT MAX(timestamp) FROM candles
                WHERE instrument_id = CAST(:iid AS uuid) AND timeframe = '5m'
                """
            ),
            {"iid": str(inst.id)},
        ).first()
        latest = row[0] if row else None
        fresh = False
        if latest:
            age_h = (datetime.now(timezone.utc) - latest.replace(tzinfo=timezone.utc)).total_seconds() / 3600
            fresh = age_h <= 48
        out[sym] = {
            "latest_5m": latest.isoformat() if latest else None,
            "live_fresh": fresh,
        }
    return out


def ranked_table(stage_a: dict[str, dict], stage_b: dict[str, dict]) -> list[dict]:
    rows = []
    for key, b in stage_b.items():
        asset = _asset_from_row(key, b)
        if asset not in PHASE1:
            continue
        br = b.get("broker_replay") or {}
        a = stage_a.get(key) or {}
        rows.append(
            {
                "key": key,
                "symbol": asset,
                "strategy": b.get("family"),
                "version": _variant_id(key),
                "timeframe": b.get("timeframe"),
                "direction": b.get("direction"),
                "classification": _classification(b),
                "robustness": b.get("robustness"),
                "stage_a_pass": bool(a.get("stage_a_pass")),
                "stage_b_trades": br.get("trades"),
                "pf": br.get("pf"),
                "expectancy_r": br.get("expectancy_r"),
                "return_pct": br.get("return_pct"),
                "max_dd_pct": br.get("max_dd_pct"),
                "positive_folds": (br.get("walk_forward") or {}).get("positive_folds"),
                "broker_fill_conversion": b.get("broker_fill_rate"),
            }
        )
    rows.sort(
        key=lambda r: (
            r["classification"] not in ("PASS", "WEAK_PASS"),
            -(r.get("expectancy_r") or -999),
            -(r.get("pf") or 0),
        ),
    )
    return rows


def main() -> None:
    stage_a = load_completed(STAGE_A)
    stage_b = load_completed(STAGE_B)
    session = sessionmaker(bind=create_engine(db_url(), pool_pre_ping=True))()
    store = TradingStore(session)
    planned = plan_v32_jobs(store)

    phase1_planned = sum(1 for j in planned if j.asset in PHASE1)
    phase1_done = sum(1 for k in stage_a if _asset_from_row(k, stage_a[k]) in PHASE1)
    survivors = sum(1 for r in stage_a.values() if r.get("stage_a_pass"))
    rejected = len(stage_a) - survivors

    old_keys = {r["key"] for r in load_v32_live_sim_active_combinations()}
    qualified = regenerate_manifest(stage_b)
    manifest = {"version": "3.2", "qualified": qualified}
    V32_QUALIFIED_MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    eligible = [r for r in qualified if v32_row_live_sim_eligible(r)]
    new_eligible = [r for r in eligible if r["key"] not in old_keys]
    phase1_new = [r for r in new_eligible if r["asset"] in PHASE1]

    cls_all = Counter(_classification(stage_b[k]) for k in stage_b)
    cls_phase1 = Counter(_classification(stage_b[k]) for k in stage_b if _asset_from_row(k, stage_b[k]) in PHASE1)
    cls_new = Counter(r["classification"] for r in new_eligible)

    fx = fx_freshness(store)
    table = ranked_table(stage_a, stage_b)
    eligible_phase1 = [r for r in table if r["classification"] in ("PASS", "WEAK_PASS")]

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "stage_a": {
            "complete": len(stage_a) >= len(planned),
            "total_jobs_planned": len(planned),
            "total_checkpoint": len(stage_a),
            "phase1_planned": phase1_planned,
            "phase1_completed": phase1_done,
            "survivors": survivors,
            "rejected": rejected,
            "breakdown": stage_a_breakdown(stage_a),
        },
        "stage_b": {
            "complete": len(stage_b) >= survivors,
            "total": len(stage_b),
            "classifications": dict(cls_all),
            "phase1_classifications": dict(cls_phase1),
        },
        "promotion": {
            "new_pass": cls_new.get("PASS", 0),
            "new_weak_pass": cls_new.get("WEAK_PASS", 0),
            "new_fail": cls_new.get("FAIL", 0),
            "new_live_eligible": len(new_eligible),
            "new_phase1_eligible": len(phase1_new),
            "total_live_eligible": len(eligible),
        },
        "fx_freshness": fx,
        "ranked_phase1": table,
        "highlights": {
            "best_new": [r["key"] for r in eligible_phase1[:8]],
            "worst_new": [r["key"] for r in table if r["classification"] == "FAIL"][:8],
            "most_active": sorted(eligible_phase1, key=lambda r: -(r.get("stage_b_trades") or 0))[:5],
        },
    }
    REPORT_OUT.write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, default=str))
    session.close()


if __name__ == "__main__":
    main()
