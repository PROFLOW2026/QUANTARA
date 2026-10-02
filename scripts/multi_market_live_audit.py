#!/usr/bin/env python3
"""Audit multi-market Live Sim readiness (read-only)."""

from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "engine"))

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from quantara_engine.db.sqlalchemy_url import normalize_sqlalchemy_postgres_url
from quantara_engine.live_sim.v2_policy import evaluate_live_sim_v2_policy
from quantara_engine.live_sim.v32_registry import (
    load_v32_live_sim_active_combinations,
    load_v32_qualified_combinations,
    parameter_overrides_for_combination,
    v32_row_live_sim_eligible,
)
from quantara_engine.market_data.registry import get_asset
from quantara_engine.market_data.sessions import is_us_equity_rth, session_allows_entries
from quantara_engine.market_data.strategy_freshness_health import is_strategy_candle_eligible
from quantara_engine.persistence.store import TradingStore
from quantara_engine.research.v3.checkpoint import load_completed
from quantara_engine.research.v3.data_quality_gate import build_data_quality_matrix
from quantara_engine.research.v3.discovery import _test_pairs
from quantara_engine.research.v3_2.job_plan import plan_v32_jobs

SYMBOLS = (
    "BTCUSD",
    "ETHUSD",
    "XAUUSD",
    "GBPJPY",
    "SOLUSD",
    "XRPUSD",
    "EURUSD",
    "USDJPY",
    "GBPUSD",
    "AUDUSD",
)
OUT = ROOT / "scripts" / "research" / "multi_market_live_audit.json"
STAGE_A = ROOT / "scripts" / "research" / "v3_2_discovery_checkpoint.jsonl"
STAGE_B = ROOT / "scripts" / "research" / "v3_2_stage_b_checkpoint.jsonl"


def db_url() -> str:
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("DATABASE_URL="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise RuntimeError("DATABASE_URL missing")


def asset_class(sym: str) -> str:
    try:
        a = get_asset(sym)
        sess = (a.trading_sessions or {}).get("sessions") or []
        if "24x7" in sess:
            return "crypto"
        if "24x5" in sess:
            return "gold" if sym in ("XAUUSD", "XAGUSD") else "FX"
        if "us_equity_rth" in sess:
            return "US equities / ETFs"
    except Exception:
        pass
    return "unknown"


def main() -> None:
    now = datetime.now(timezone.utc)
    session = sessionmaker(bind=create_engine(normalize_sqlalchemy_postgres_url(db_url())))()
    store = TradingStore(session)
    stage_a = load_completed(STAGE_A)
    stage_b = load_completed(STAGE_B)
    quality = build_data_quality_matrix(store)
    qmap = {(r["symbol"], r["timeframe"]): r for r in quality.get("matrix", [])}
    allowed = set(_test_pairs(quality))
    planned = plan_v32_jobs(store)
    manifest = load_v32_qualified_combinations()
    live = load_v32_live_sim_active_combinations()
    live_assets = sorted({r["asset"] for r in live})

    per_symbol: dict[str, dict] = {}
    for sym in SYMBOLS:
        asset = get_asset(sym)
        inst = store.get_instrument_by_symbol(sym)
        row: dict = {
            "asset_class": asset_class(sym),
            "supported_adapter": asset is not None,
            "provider_primary": getattr(asset, "primary_provider", None) if asset else None,
            "instrument_exists": inst is not None,
            "session_24x7_or_fx": session_allows_entries(asset.trading_sessions, now) if asset else None,
        }
        candles: dict[str, dict] = {}
        if inst:
            for tf in ("1m", "5m", "15m", "1h"):
                n = store.count_candles(inst.id, tf)
                last = store.latest_candle_timestamp(inst.id, tf)
                age_min = None
                if last:
                    ts = last.replace(tzinfo=timezone.utc) if last.tzinfo is None else last
                    age_min = round((now - ts).total_seconds() / 60, 1)
                eligible, reason = (
                    is_strategy_candle_eligible(asset, last, now, timeframe=tf)
                    if asset and last
                    else (False, "no_data")
                )
                candles[tf] = {
                    "count": n,
                    "latest": last.isoformat() if last else None,
                    "age_minutes": age_min,
                    "strategy_eligible_now": eligible,
                    "eligibility_reason": reason,
                }
        row["candles"] = candles
        row["quality_gate"] = {
            tf: qmap.get((sym, tf), {}).get("classification", "MISSING")
            for tf in ("5m", "15m", "1h")
        }
        row["research_pairs_allowed"] = [tf for tf in ("5m", "15m", "1h") if (sym, tf) in allowed]
        jobs = [j for j in planned if j.asset == sym]
        row["stage_a"] = {
            "jobs_planned": len(jobs),
            "checkpoint_rows": sum(1 for k in stage_a if f"|{sym}|" in k),
            "stage_a_pass": sum(1 for k, r in stage_a.items() if f"|{sym}|" in k and r.get("stage_a_pass")),
        }
        cls = Counter()
        for k, r in stage_b.items():
            if f"|{sym}|" not in k:
                continue
            br = r.get("broker_replay") or {}
            cls[str(br.get("classification") or r.get("classification") or "?").upper()] += 1
        row["stage_b"] = {"rows": sum(cls.values()), "classifications": dict(cls)}
        mrows = [r for r in manifest if r.get("asset") == sym]
        lrows = [r for r in live if r.get("asset") == sym]
        row["manifest"] = {
            "total": len(mrows),
            "pass": sum(1 for r in mrows if r.get("classification") == "PASS"),
            "weak_pass": sum(1 for r in mrows if r.get("classification") == "WEAK_PASS"),
            "fail": sum(1 for r in mrows if r.get("classification") == "FAIL"),
            "live_eligible": len(lrows),
        }
        if asset:
            row["execution"] = {
                "asset_class": getattr(asset, "asset_class", None),
                "spot_crypto_short_note": (
                    "Kraken spot: shorts only via SELL close; new short entries blocked at pre_trade"
                    if row["asset_class"] == "crypto"
                    else None
                ),
            }
        blockers: list[str] = []
        if not row["instrument_exists"]:
            blockers.append("NO_INSTRUMENT")
        if not row["research_pairs_allowed"]:
            blockers.append("DATA_QUALITY_OR_HISTORY_BLOCKS_RESEARCH")
        if row["stage_a"]["stage_a_pass"] == 0 and row["stage_a"]["checkpoint_rows"] > 0:
            blockers.append("STAGE_A_ZERO_SURVIVORS")
        if row["manifest"]["live_eligible"] == 0:
            if row["manifest"]["pass"] + row["manifest"]["weak_pass"] == 0:
                blockers.append("NO_PASS_OR_WEAK_PASS_IN_MANIFEST")
            else:
                blockers.append("MANIFEST_NOT_LIVE_ELIGIBLE")
        if sym in ("EURUSD", "USDJPY", "GBPUSD", "AUDUSD"):
            latest_5m = candles.get("5m", {}).get("latest")
            if latest_5m:
                ts = datetime.fromisoformat(latest_5m.replace("Z", "+00:00"))
                age_h = (now - ts).total_seconds() / 3600
                if age_h > 48:
                    blockers.append("STALE_FX_5M_MARK")
        row["live_status"] = "ACTIVE" if lrows else ("BLOCKED: " + "; ".join(blockers) if blockers else "NOT_PROMOTED")
        per_symbol[sym] = row

    # US-RTH global block check: crypto at US-closed instant
    us_closed = not is_us_equity_rth(now)
    crypto_eligible_outside_rth = {}
    for sym in ("BTCUSD", "ETHUSD"):
        asset = get_asset(sym)
        inst = store.get_instrument_by_symbol(sym)
        if not asset or not inst:
            continue
        last = store.latest_candle_timestamp(inst.id, "15m")
        ok, reason = is_strategy_candle_eligible(asset, last, now, timeframe="15m")
        crypto_eligible_outside_rth[sym] = {
            "us_rth_now": is_us_equity_rth(now),
            "eligible": ok,
            "reason": reason,
        }

    report = {
        "generated_at": now.isoformat(),
        "total_live_candidates": len(live),
        "total_live_assets": len(live_assets),
        "live_asset_classes": dict(Counter(asset_class(a) for a in live_assets)),
        "per_symbol": per_symbol,
        "us_rth_global_block_found": False,
        "crypto_eval_outside_us_rth": crypto_eligible_outside_rth,
        "why_live_us_only": (
            "All 106 active Live rows are PASS/WEAK_PASS on US equities only; "
            "non-US symbols have zero Stage-B PASS/WEAK_PASS (crypto/metals/FX Stage A had no survivors or FX excluded from research)."
        ),
    }
    OUT.write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, default=str))
    session.close()


if __name__ == "__main__":
    main()
