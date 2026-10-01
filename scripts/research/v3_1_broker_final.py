#!/usr/bin/env python3
"""V3.1 final broker replay + P2/P3 portfolio selection."""

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
from quantara_engine.research.v3_1.broker_replay import (
    _chronological_portfolio,
    monte_carlo_equity,
    passive_buy_hold,
    replay_finalist_broker,
    verify_parameter_fidelity,
)

OUT = ROOT / "scripts" / "research" / "v3_1_broker_final_report.json"
REPLAY_REPORT = ROOT / "scripts" / "research" / "v3_1_realistic_replay_report.json"

FINALISTS = [
    {
        "key": "vwap_mean_reversion|v2|AMD|5m|long",
        "family": "vwap_mean_reversion",
        "asset": "AMD",
        "timeframe": "5m",
        "direction": "long",
        "parameters": {"dev": 0.005},
    },
    {
        "key": "atr_trend|v2|NVDA|15m|long",
        "family": "atr_trend",
        "asset": "NVDA",
        "timeframe": "15m",
        "direction": "long",
        "parameters": {"ema_fast": 20, "ema_slow": 50},
    },
    {
        "key": "atr_trend|v1|AMD|15m|long",
        "family": "atr_trend",
        "asset": "AMD",
        "timeframe": "15m",
        "direction": "long",
        "parameters": {"ema_fast": 15, "ema_slow": 40},
    },
]


def db_url() -> str:
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("DATABASE_URL="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise RuntimeError("DATABASE_URL missing")


def fix_survivor_count() -> dict:
    rep = json.loads(REPLAY_REPORT.read_text(encoding="utf-8"))
    keys = [
        c["key"]
        for c in rep.get("candidates") or []
        if c.get("classification") == "SURVIVES"
    ]
    unique = sorted(set(keys))
    rep["unique_realistic_survivors"] = unique
    rep["survivors_count"] = len(unique)
    rep["survivor_count_note"] = (
        "Four unique SURVIVES in candidates[]; fourth is vwap_mean_reversion|v1|AMD|5m|long "
        "(near-duplicate of v2 AMD 5m — excluded from Live Sim finalists)."
        if len(unique) == 4
        else None
    )
    REPLAY_REPORT.write_text(json.dumps(rep, indent=2, default=str), encoding="utf-8")
    return {"unique": unique, "count": len(unique)}


def choose_portfolio(p2: dict, p3: dict) -> str:
    p2_score = (p2.get("return_pct") or 0) / max(p2.get("max_dd_pct") or 1, 1)
    p3_score = (p3.get("return_pct") or 0) / max(p3.get("max_dd_pct") or 1, 1)
    if (p3.get("amd_concentration_pct") or 0) > 55 and p3_score <= p2_score * 1.05:
        return "P2"
    if p3_score > p2_score * 1.08 and (p3.get("trades") or 0) >= (p2.get("trades") or 0):
        return "P3"
    return "P2"


def main() -> None:
    survivor_fix = fix_survivor_count()
    param_check = {}

    engine = create_engine(db_url())
    Session = sessionmaker(bind=engine)
    session = Session()
    store = TradingStore(session)
    param_check = verify_parameter_fidelity(store)

    results: list[dict] = []
    amd_candles: list = []
    nvda_candles: list = []
    try:
        amd_inst = store.get_instrument_by_symbol("AMD")
        nvda_inst = store.get_instrument_by_symbol("NVDA")
        amd_candles = [
            c for c in store.list_candles(amd_inst.id, "15m") if c.timestamp >= V3_RESEARCH_START
        ]
        nvda_candles = [
            c for c in store.list_candles(nvda_inst.id, "15m") if c.timestamp >= V3_RESEARCH_START
        ]
        for i, spec in enumerate(FINALISTS, 1):
            print(f"broker replay {i}/3 {spec['key']}", flush=True)
            inst = store.get_instrument_by_symbol(spec["asset"])
            candles = [
                c
                for c in store.list_candles(inst.id, spec["timeframe"])
                if c.timestamp >= V3_RESEARCH_START
            ]
            rep = replay_finalist_broker(
                store,
                key=spec["key"],
                family=spec["family"],
                asset=spec["asset"],
                timeframe=spec["timeframe"],
                direction=spec["direction"],
                parameters=spec["parameters"],
                instrument=inst,
                candles=candles,
            )
            results.append(rep)
        session.rollback()
    finally:
        session.close()

    by_key = {r["key"]: r for r in results}
    a, b, c = [by_key[f["key"]] for f in FINALISTS]

    p2 = _chronological_portfolio([a["trade_rows"], b["trade_rows"]])
    p3 = _chronological_portfolio([a["trade_rows"], b["trade_rows"], c["trade_rows"]], amd_combined_sleeve=True)
    selected = choose_portfolio(p2, p3)
    port = p3 if selected == "P3" else p2
    mc = monte_carlo_equity(port.get("chronological_pnls") or [])

    amd_bh = passive_buy_hold(amd_candles)
    nvda_bh = passive_buy_hold(nvda_candles)

    eq_bh = (amd_bh["return_pct"] + nvda_bh["return_pct"]) / 2
    dd_bh = (amd_bh["max_dd_pct"] + nvda_bh["max_dd_pct"]) / 2
    passive_eq = {"return_pct": eq_bh, "max_dd_pct": dd_bh, "return_over_dd": eq_bh / max(dd_bh, 0.01)}
    v31_rod = (port.get("return_pct") or 0) / max(port.get("max_dd_pct") or 1, 1)

    pass_count = sum(1 for r in results if r.get("classification") == "PASS")
    weak = sum(1 for r in results if r.get("classification") == "WEAK_PASS")

    for r in results:
        r.pop("trade_rows", None)
    if "chronological_pnls" in p2:
        p2.pop("chronological_pnls", None)
    if "chronological_pnls" in p3:
        p3.pop("chronological_pnls", None)

    report = {
        "unique_realistic_survivors": survivor_fix["unique"],
        "unique_survivor_count": survivor_fix["count"],
        "parameter_fidelity": param_check,
        "full_realistic_broker_v1_replay": True,
        "finalists": results,
        "pass_count": pass_count,
        "weak_pass_count": weak,
        "at_least_two_pass": pass_count + weak >= 2,
        "portfolio_p2": p2,
        "portfolio_p3": p3,
        "selected_portfolio": selected,
        "monte_carlo_5000": mc,
        "passive_amd_15m": amd_bh,
        "passive_nvda_15m": nvda_bh,
        "passive_equal_weight_proxy": passive_eq,
        "v31_portfolio": {
            "return_pct": port.get("return_pct"),
            "max_dd_pct": port.get("max_dd_pct"),
            "return_over_dd": round(v31_rod, 4),
        },
        "v31_better_risk_adjusted": v31_rod > passive_eq["return_over_dd"],
    }
    OUT.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(json.dumps({"ok": True, "path": str(OUT), "pass": pass_count, "selected": selected}, indent=2))


if __name__ == "__main__":
    main()
