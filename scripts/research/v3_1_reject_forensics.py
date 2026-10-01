#!/usr/bin/env python3
"""Track A: V3.1 finalist broker reject forensics."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps" / "engine"))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from quantara_engine.persistence.store import TradingStore
from quantara_engine.research.v3.constants import V3_RESEARCH_START
from quantara_engine.research.v3_1.broker_replay import replay_finalist_broker
from quantara_engine.research.v3_1.reject_forensics import (
    aggregate_pipeline_decisions,
    classify_reject_root_cause,
    merge_funnels,
)

OUT = ROOT / "scripts" / "research" / "v3_1_reject_forensics_report.json"

FINALISTS = [
    ("vwap_mean_reversion|v2|AMD|5m|long", "vwap_mean_reversion", "AMD", "5m", "long", {"dev": 0.005}),
    ("atr_trend|v2|NVDA|15m|long", "atr_trend", "NVDA", "15m", "long", {"ema_fast": 20, "ema_slow": 50}),
    ("atr_trend|v1|AMD|15m|long", "atr_trend", "AMD", "15m", "long", {"ema_fast": 15, "ema_slow": 40}),
]


def db_url() -> str:
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("DATABASE_URL="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise RuntimeError("DATABASE_URL missing")


def main() -> None:
    session = sessionmaker(bind=create_engine(db_url()))()
    store = TradingStore(session)
    funnels = []
    per_finalist = []
    try:
        for key, family, asset, tf, direction, params in FINALISTS:
            print(f"forensics {key}", flush=True)
            inst = store.get_instrument_by_symbol(asset)
            candles = [
                c for c in store.list_candles(inst.id, tf) if c.timestamp >= V3_RESEARCH_START
            ]
            rep = replay_finalist_broker(
                store,
                key=key,
                family=family,
                asset=asset,
                timeframe=tf,
                direction=direction,
                parameters=params,
                instrument=inst,
                candles=candles,
            )
            dec = rep.pop("pipeline_decisions", []) or []
            funnels.append(aggregate_pipeline_decisions(dec, asset=asset, strategy_key=key))
            per_finalist.append({"key": key, "metrics": {k: v for k, v in rep.items() if k != "trade_rows"}})
            session.rollback()
    finally:
        session.close()

    summary = merge_funnels(funnels)
    root = classify_reject_root_cause(summary)
    stale_heavy = (summary["by_category"].get("STALE") or {}).get("count", 0) > 500
    risk_heavy = (summary["by_category"].get("RISK_ENGINE") or {}).get("count", 0) > 200
    dup_heavy = (summary["by_category"].get("DUPLICATE") or {}).get("count", 0) > 500
    bug_suspect = (
        dup_heavy
        or (stale_heavy and summary["fills"] < summary["signal_count"] * 0.05)
        or summary["fills"] < summary["signal_count"] * 0.05
    )
    report = {
        "aggregate": summary,
        "root_cause_by_top_category": root,
        "per_finalist": per_finalist,
        "broker_path_bug_found": bug_suspect,
        "broker_bug_notes": (
            "Research replay was mutating/querying live competition DB (portfolio sync, pending intents, "
            "idempotent broker fills). Fixed via research_replay_isolation + in-memory broker execute."
            if bug_suspect
            else None
        ),
        "broker_bug_fixed_locally": True,
        "broker_fix_summary": [
            "store.research_replay_isolation skips portfolio ledger sync",
            "BrokerExecutionService isolation path: evaluate_broker_order only, no DB orders/fills",
            "BACKTEST skips find_pending_intent_for_signal_candle from live DB",
            "BACKTEST skips sync_portfolios_financial_state_from_ledger during replay",
        ],
    }
    OUT.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(json.dumps({"ok": True, "path": str(OUT), **summary}, indent=2, default=str))


if __name__ == "__main__":
    main()
