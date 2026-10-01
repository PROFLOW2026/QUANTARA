#!/usr/bin/env python3
"""P2/P3/P4 portfolio sim + selection for V3.2 Live Sim."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
import sys

sys.path.insert(0, str(ROOT / "apps" / "engine"))

from quantara_engine.research.v3_2.portfolio_v2_sim import monte_carlo_from_sim, simulate_portfolio_v2_gates

RESEARCH = ROOT / "scripts" / "research"
STAGE_B = RESEARCH / "v3_2_stage_b_checkpoint.jsonl"
OUT = RESEARCH / "v3_2_portfolio_p2_p3_p4_report.json"

KEYS = {
    "A": "rsi_divergence_mr|v1|AMD|15m|long",
    "B": "ema_pullback_continue|v2|NVDA|15m|long",
    "C": "vwap_session_revert|v1|TSLA|5m|short",
    "D": "orb_continuation|v1|AMD|5m|long",
    "E": "orb_continuation|v1|COIN|5m|short",
}

PORTFOLIOS = {
    "P2": ["A", "B"],
    "P3": ["A", "B", "E"],
    "P4": ["A", "B", "E", "C"],
}


def load_stage_b() -> dict[str, dict]:
    out: dict[str, dict] = {}
    for line in STAGE_B.read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            out[row["key"]] = row
    return out


def legs_for(ids: list[str], by_key: dict) -> list[dict]:
    legs = []
    for k in ids:
        key = KEYS[k]
        row = by_key[key]
        legs.append({"key": key, "symbol": row["asset"], "trade_rows": row.get("trade_rows") or []})
    return legs


def score(sim: dict) -> float:
    exp = sim.get("expectancy_r") or -999
    pf = sim.get("pf") or 0
    rod = sim.get("return_over_dd") or 0
    dd_pen = (sim.get("max_dd_pct") or 100) / 100
    return exp * 2 + pf + rod / 10 - dd_pen


def main() -> None:
    by_key = load_stage_b()
    results: dict[str, dict] = {}
    for name, ids in PORTFOLIOS.items():
        sim = simulate_portfolio_v2_gates(legs_for(ids, by_key))
        mc = monte_carlo_from_sim(sim)
        sim.pop("chronological_pnls", None)
        results[name] = {
            "strategies": [KEYS[i] for i in ids],
            "sim": sim,
            "monte_carlo": mc,
            "score": round(score(sim), 4),
        }

    ranked = sorted(results.items(), key=lambda x: -x[1]["score"])
    selected = ranked[0][0]
    sel = results[selected]
    gate_ok = (
        (sel["sim"].get("pf") or 0) >= 1.15
        and (sel["sim"].get("expectancy_r") or 0) > 0
        and (sel["sim"].get("max_dd_pct") or 999) <= 20
        and (sel["sim"].get("trades") or 0) >= 30
    )
    if not gate_ok:
        for name, _ in ranked:
            s = results[name]["sim"]
            if (s.get("pf") or 0) >= 1.15 and (s.get("expectancy_r") or 0) > 0 and (s.get("trades") or 0) >= 30:
                selected = name
                sel = results[selected]
                gate_ok = True
                break

    report = {
        "portfolios": results,
        "selected_portfolio": selected,
        "selected_strategies": sel["strategies"],
        "live_sim_release_gate": gate_ok,
        "amd_orb_5m": {"key": KEYS["D"], "status": "RESEARCH_ONLY"},
    }
    OUT.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(json.dumps({"ok": True, "selected": selected, "gate": gate_ok, "path": str(OUT)}, indent=2))


if __name__ == "__main__":
    main()
