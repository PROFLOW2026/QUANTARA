#!/usr/bin/env python3
"""Non-US V3.2 profit discovery — Waves 1–2, Stage A/B, merge manifest (no Live reset)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import traceback
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps" / "engine"))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from quantara_engine.live_sim.v32_registry import V32_QUALIFIED_MANIFEST, load_v32_live_sim_active_combinations
from quantara_engine.persistence.store import TradingStore
from quantara_engine.research.v3.checkpoint import append_result, load_completed
from quantara_engine.research.v3.constants import V3_RESEARCH_START
from quantara_engine.research.v3.job_registry import claim_job, finish_job, init_registry_jobs, registry_lock
from quantara_engine.research.v3_2.non_us_job_plan import CRYPTO, FX_CORE, GOLD, plan_non_us_jobs
from quantara_engine.research.v3_2.non_us_run_job import run_non_us_stage_a_job
from quantara_engine.research.v3_2.stage_b import validate_survivor_broker

RESEARCH = ROOT / "scripts" / "research"
CHECKPOINT_A = RESEARCH / "non_us_v3_2_discovery_checkpoint.jsonl"
CHECKPOINT_B = RESEARCH / "non_us_v3_2_stage_b_checkpoint.jsonl"
REGISTRY_A = RESEARCH / "non_us_v3_2_discovery_jobs.json"
REG_LOCK_A = RESEARCH / ".non_us_v3_2_discovery_jobs.lock"
REGISTRY_B = RESEARCH / "non_us_v3_2_stage_b_jobs.json"
REG_LOCK_B = RESEARCH / ".non_us_v3_2_stage_b_jobs.lock"
OUT = RESEARCH / "non_us_profit_discovery_report.json"

NON_US_ASSETS = CRYPTO | GOLD | FX_CORE


def db_url() -> str:
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("DATABASE_URL="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise RuntimeError("DATABASE_URL missing")


def _classification(row: dict) -> str:
    br = row.get("broker_replay") or {}
    return str(br.get("classification") or row.get("classification") or "FAIL").upper()


def merge_manifest_from_stage_b(stage_b: dict[str, dict]) -> int:
    existing = json.loads(V32_QUALIFIED_MANIFEST.read_text(encoding="utf-8"))
    by_key = {r["key"]: r for r in existing.get("qualified") or []}
    updated = 0
    for key, row in stage_b.items():
        asset = str(row.get("asset") or key.split("|")[2]).upper()
        if asset not in NON_US_ASSETS:
            continue
        cls = _classification(row)
        br = row.get("broker_replay") or {}
        by_key[key] = {
            "key": key,
            "family": row.get("family") or key.split("|")[0],
            "variant_id": key.split("|")[1] if "|" in key else "v1",
            "asset": asset,
            "timeframe": row.get("timeframe") or key.split("|")[3],
            "direction": row.get("direction") or key.split("|")[4],
            "parameters": row.get("parameters") or {},
            "robustness": row.get("robustness") or "FAIL",
            "classification": cls,
        }
        updated += 1
    existing["qualified"] = sorted(by_key.values(), key=lambda r: r["key"])
    V32_QUALIFIED_MANIFEST.write_text(json.dumps(existing, indent=2) + "\n", encoding="utf-8")
    return updated


def run_wave(store: TradingStore, wave: int, cache: dict, pid: int) -> tuple[int, int]:
    jobs = plan_non_us_jobs(store, wave=wave)
    init_registry_jobs(
        REGISTRY_A,
        REG_LOCK_A,
        [{"key": j.key, "candidate_id": j.key, "asset": j.asset, "timeframe": j.timeframe} for j in jobs],
    )
    completed_a = load_completed(CHECKPOINT_A)
    stage_a_new = 0
    for job in jobs:
        if job.key in completed_a:
            continue
        if not claim_job(registry_path=REGISTRY_A, lock_path=REG_LOCK_A, job_key=job.key, pid=pid):
            continue
        inst = store.get_instrument_by_symbol(job.asset)
        if not inst:
            finish_job(registry_path=REGISTRY_A, lock_path=REG_LOCK_A, job_key=job.key, pid=pid, status="FAILED")
            continue
        ck = (str(inst.id), job.timeframe)
        if ck not in cache:
            cache[ck] = [c for c in store.list_candles(inst.id, job.timeframe) if c.timestamp >= V3_RESEARCH_START]
        if len(cache[ck]) < 250:
            finish_job(registry_path=REGISTRY_A, lock_path=REG_LOCK_A, job_key=job.key, pid=pid, status="FAILED")
            continue
        try:
            row = run_non_us_stage_a_job(job=job, candles=cache[ck], store=store, instrument=inst)
            if row is None:
                finish_job(registry_path=REGISTRY_A, lock_path=REG_LOCK_A, job_key=job.key, pid=pid, status="FAILED")
                continue
            with registry_lock(REG_LOCK_A):
                append_result(CHECKPOINT_A, row)
            stage_a_new += 1
            finish_job(registry_path=REGISTRY_A, lock_path=REG_LOCK_A, job_key=job.key, pid=pid, status="DONE")
            store.session.rollback()
        except Exception:
            traceback.print_exc()
            finish_job(registry_path=REGISTRY_A, lock_path=REG_LOCK_A, job_key=job.key, pid=pid, status="FAILED")
            store.session.rollback()

    completed_a = load_completed(CHECKPOINT_A)
    survivors = [j for j in jobs if completed_a.get(j.key, {}).get("stage_a_pass")]
    init_registry_jobs(
        REGISTRY_B,
        REG_LOCK_B,
        [{"key": j.key, "candidate_id": j.key, "asset": j.asset, "timeframe": j.timeframe} for j in survivors],
    )
    completed_b = load_completed(CHECKPOINT_B)
    stage_b_new = 0
    for job in survivors:
        if job.key in completed_b:
            continue
        if not claim_job(registry_path=REGISTRY_B, lock_path=REG_LOCK_B, job_key=job.key, pid=pid):
            continue
        inst = store.get_instrument_by_symbol(job.asset)
        ck = (str(inst.id), job.timeframe)
        if ck not in cache:
            cache[ck] = [c for c in store.list_candles(inst.id, job.timeframe) if c.timestamp >= V3_RESEARCH_START]
        try:
            row = validate_survivor_broker(job=job, candles=cache[ck], store=store, instrument=inst)
            with registry_lock(REG_LOCK_B):
                append_result(CHECKPOINT_B, row)
            stage_b_new += 1
            finish_job(registry_path=REGISTRY_B, lock_path=REG_LOCK_B, job_key=job.key, pid=pid, status="DONE")
            store.session.rollback()
        except Exception:
            traceback.print_exc()
            finish_job(registry_path=REGISTRY_B, lock_path=REG_LOCK_B, job_key=job.key, pid=pid, status="FAILED")
            store.session.rollback()
    return stage_a_new, stage_b_new


def asset_summary(stage_a: dict, stage_b: dict, asset: str) -> dict:
    keys_a = [k for k in stage_a if f"|{asset}|" in k]
    keys_b = [k for k in stage_b if f"|{asset}|" in k]
    cls = Counter(_classification(stage_b[k]) for k in keys_b)
    live = len([k for k in keys_b if _classification(stage_b[k]) in ("PASS", "WEAK_PASS")])
    return {
        "stage_a_jobs": len(keys_a),
        "survivors": sum(1 for k in keys_a if stage_a[k].get("stage_a_pass")),
        "stage_b": len(keys_b),
        "pass": cls.get("PASS", 0),
        "weak_pass": cls.get("WEAK_PASS", 0),
        "fail": cls.get("FAIL", 0),
        "live_candidates": live,
    }


def main() -> None:
    session = sessionmaker(bind=create_engine(db_url(), pool_pre_ping=True))()
    store = TradingStore(session)
    pid = os.getpid()
    cache: dict = {}
    w1 = run_wave(store, 1, cache, pid)
    w2 = run_wave(store, 2, cache, pid)
    stage_a = load_completed(CHECKPOINT_A)
    stage_b = load_completed(CHECKPOINT_B)
    merged = merge_manifest_from_stage_b(stage_b)
    session.close()

    before = len(load_v32_live_sim_active_combinations())

    subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "sync_v32_live_sim_candidates.py")],
        cwd=str(ROOT),
        check=False,
    )

    from quantara_engine.live_sim import v32_registry as v32reg

    v32reg.load_v32_qualified_combinations.cache_clear()
    after = len(v32reg.load_v32_live_sim_active_combinations())

    symbols = [
        "BTCUSD",
        "ETHUSD",
        "XAUUSD",
        "GBPJPY",
        "SOLUSD",
        "XRPUSD",
        "EURUSD",
        "USDJPY",
    ]
    per = {sym: asset_summary(stage_a, stage_b, sym) for sym in symbols}
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "waves": {"wave1_new": w1, "wave2_new": w2},
        "manifest_keys_merged": merged,
        "live_candidates_before": before,
        "live_candidates_after_sync": after,
        "per_symbol": per,
        "new_non_us_live": after - before,
    }
    OUT.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
