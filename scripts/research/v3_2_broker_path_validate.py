#!/usr/bin/env python3
"""3-candidate broker path proof after forensics fix."""

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

OUT = ROOT / "scripts" / "research" / "v3_2_broker_path_validate.json"

CANDIDATES = [
    ("vwap_mean_reversion|v2|AMD|5m|long", "vwap_mean_reversion", "AMD", "5m", "long", {"dev": 0.005}),
    ("atr_trailing_trend|v1|AMD|15m|long", "atr_trailing_trend", "AMD", "15m", "long", {"ema_slow": 55}),
    ("rsi_divergence_mr|v1|AMD|15m|long", "rsi_divergence_mr", "AMD", "15m", "long", {}),
]


def db_url() -> str:
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("DATABASE_URL="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise RuntimeError("DATABASE_URL missing")


def main() -> None:
    session = sessionmaker(bind=create_engine(db_url()))()
    store = TradingStore(session)
    rows = []
    try:
        for key, family, asset, tf, direction, params in CANDIDATES:
            inst = store.get_instrument_by_symbol(asset)
            candles = [c for c in store.list_candles(inst.id, tf) if c.timestamp >= V3_RESEARCH_START]
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
            sig = rep.get("signal_count") or 0
            fills = rep.get("fills") or rep.get("trades") or 0
            attempts = rep.get("orders") or sig
            rejects = max(0, attempts - fills)
            conv = round(fills / sig * 100, 2) if sig else 0.0
            rows.append(
                {
                    "key": key,
                    "signals": sig,
                    "broker_attempts": attempts,
                    "fills": fills,
                    "rejects": rejects,
                    "fill_conversion_pct": conv,
                    "pf": rep.get("pf"),
                    "expectancy_r": rep.get("expectancy_r"),
                }
            )
            session.rollback()
    finally:
        session.close()
    pass_gate = any(r["fill_conversion_pct"] >= 5 and r["fills"] >= 10 for r in rows)
    payload = {"candidates": rows, "validation": "PASS" if pass_gate else "FAIL", "pass_gate": pass_gate}
    OUT.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
