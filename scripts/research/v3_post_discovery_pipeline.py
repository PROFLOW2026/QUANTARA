#!/usr/bin/env python3
"""Post-discovery V3 analysis: benchmarks, portfolio, Monte Carlo, $10k @ 0.25%."""

from __future__ import annotations

import json
import random
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps" / "engine"))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from quantara_engine.persistence.store import TradingStore
from quantara_engine.research.v3.backtest_lab import (
    filter_trades_by_window,
    run_candidate_backtest,
    trades_to_r_pnls,
)
from quantara_engine.research.v3.candidates import V3Candidate, frozen_v3_candidates
from quantara_engine.research.v3.constants import V3_INITIAL_LIVE_RISK_PCT, V3_RESEARCH_START
from quantara_engine.research.v3.discovery import _chronological_folds, _monte_carlo
from quantara_engine.research.v3.metrics import profit_factor, trade_metrics

RESEARCH = ROOT / "scripts" / "research"
DISCOVERY_REPORT = RESEARCH / "v3_discovery_report.json"
OUT = RESEARCH / "v3_final_outcome.json"


def db_url() -> str:
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("DATABASE_URL="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise RuntimeError("DATABASE_URL missing")


def _candidate_map(candidates: list[V3Candidate] | None = None) -> dict[str, V3Candidate]:
    src = candidates if candidates is not None else frozen_v3_candidates()
    return {c.candidate_id: c for c in src}


def _buy_hold_benchmark(candles: list, *, direction: str = "long") -> dict[str, Any]:
    if len(candles) < 2:
        return {"return_pct": None, "trades": 0}
    start = float(candles[0].close)
    end = float(candles[-1].close)
    if direction == "short":
        ret = (start - end) / start * 100.0
    else:
        ret = (end - start) / start * 100.0
    return {"return_pct": ret, "trades": 1}


def _ma_cross_benchmark(candles: list, *, fast: int = 20, slow: int = 50) -> dict[str, Any]:
    if len(candles) < slow + 5:
        return {"return_pct": None, "trades": 0}
    closes = [float(c.close) for c in candles]
    eq = 0.0
    pos = 0
    entry = 0.0
    trades = 0
    for i in range(slow, len(closes)):
        ma_f = sum(closes[i - fast : i]) / fast
        ma_s = sum(closes[i - slow : i]) / slow
        px = closes[i]
        if pos == 0 and ma_f > ma_s:
            pos = 1
            entry = px
            trades += 1
        elif pos == 1 and ma_f < ma_s:
            eq += (px - entry) / entry
            pos = 0
    if pos == 1:
        eq += (closes[-1] - entry) / entry
    return {"return_pct": eq * 100.0, "trades": trades}


def _direction_row(row: dict[str, Any]) -> dict[str, Any]:
    long_m = row.get("long") or {}
    short_m = row.get("short") or {}
    le = long_m.get("expectancy_r")
    se = short_m.get("expectancy_r")
    if le is not None and (se is None or le >= se):
        return {"direction": "long", **long_m}
    if se is not None:
        return {"direction": "short", **short_m}
    return {"direction": "both", **(row.get("oos") or {})}


def _simulate_10k(
    oos_r_returns: list[float],
    *,
    start_equity: float = 10_000.0,
    risk_pct: float = V3_INITIAL_LIVE_RISK_PCT,
) -> dict[str, Any]:
    equity = start_equity
    peak = equity
    max_dd = 0.0
    wins = 0.0
    losses = 0.0
    risk_dollars = start_equity * (risk_pct / 100.0)
    util_bars = 0
    for r in oos_r_returns:
        pnl = r * risk_dollars
        equity += pnl
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
        if pnl >= 0:
            wins += pnl
        else:
            losses += abs(pnl)
        util_bars += 1
    pf = (wins / losses) if losses > 0 else (None if wins == 0 else float("inf"))
    return {
        "starting_equity": start_equity,
        "ending_equity": round(equity, 2),
        "pnl": round(equity - start_equity, 2),
        "return_pct": round((equity / start_equity - 1.0) * 100.0, 4),
        "pf": pf if pf != float("inf") else None,
        "max_dd": round(max_dd, 2),
        "trades": len(oos_r_returns),
        "capital_utilization": round(util_bars * risk_pct / 100.0, 4),
        "risk_pct_per_trade": risk_pct,
    }


def run_pipeline(
    store: TradingStore,
    report: dict[str, Any],
    *,
    candidates: list[V3Candidate] | None = None,
) -> dict[str, Any]:
    robust = [r for r in report.get("top10", []) if r.get("robustness") == "ROBUST"]
    if not robust:
        robust = [r for r in report.get("top10", []) if r.get("robustness") == "PROMISING"]
    finalists = robust[:6]
    cmap = _candidate_map(candidates)
    enriched: list[dict[str, Any]] = []
    portfolio_r: list[float] = []

    for row in finalists:
        cand = cmap.get(row["candidate_id"])
        if not cand:
            continue
        instrument = store.get_instrument_by_symbol(row["asset"])
        if not instrument:
            continue
        candles = [
            c
            for c in store.list_candles(instrument.id, row["timeframe"])
            if c.timestamp >= V3_RESEARCH_START
        ]
        if len(candles) < 250:
            continue
        bt = run_candidate_backtest(
            candidate=cand,
            instrument=instrument,
            timeframe=row["timeframe"],
            candles=candles,
        )
        trades = bt.get("trades") or []
        start, end = candles[0].timestamp, candles[-1].timestamp
        folds = _chronological_folds(start, end, n=4)
        oos_trades = filter_trades_by_window(trades, folds[-1][0], folds[-1][1])
        pnls, risks = trades_to_r_pnls(oos_trades)
        oos_rs = [p / r for p, r in zip(pnls, risks) if r] if pnls else []
        dir_info = _direction_row(row)
        enriched.append(
            {
                "strategy": cand.strategy_slug,
                "candidate_id": cand.candidate_id,
                "family": cand.family,
                "asset": row["asset"],
                "timeframe": row["timeframe"],
                "direction": dir_info.get("direction"),
                "parameters": cand.parameters,
                "oos_trades": len(oos_trades),
                "oos_pf": profit_factor(pnls),
                "oos_expectancy_r": sum(oos_rs) / len(oos_rs) if oos_rs else None,
                "positive_folds": (row.get("walk_forward") or {}).get("positive_folds"),
                "total_folds": (row.get("walk_forward") or {}).get("folds"),
                "robustness": row.get("robustness"),
                "benchmarks": {
                    "buy_hold": _buy_hold_benchmark(candles),
                    "ma_cross_20_50": _ma_cross_benchmark(candles),
                },
                "mae_mfe": trade_metrics(pnls, risk_usd=risks),
            }
        )
        portfolio_r.extend(oos_rs)

    mc = _monte_carlo(portfolio_r) if portfolio_r else report.get("monte_carlo_best") or {}
    sim = _simulate_10k(portfolio_r) if portfolio_r else {}

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "finalists": enriched,
        "robust_count": len([r for r in report.get("top10", []) if r.get("robustness") == "ROBUST"]),
        "promising_count": report.get("robustness_counts", {}).get("PROMISING", 0),
        "monte_carlo": mc,
        "sim_10k_0_25pct": sim,
        "benchmark_note": "buy_hold and simple MA cross on same OOS window candles",
        "realistic_broker_v1": "costs embedded in backtest_lab execution_assumptions_for",
    }


def main() -> None:
    if not DISCOVERY_REPORT.is_file():
        print(json.dumps({"error": "missing_discovery_report", "path": str(DISCOVERY_REPORT)}))
        sys.exit(1)
    report = json.loads(DISCOVERY_REPORT.read_text(encoding="utf-8"))
    session = sessionmaker(bind=create_engine(db_url()))()
    store = TradingStore(session)
    outcome = run_pipeline(store, report)
    session.close()
    OUT.write_text(json.dumps(outcome, indent=2, default=str), encoding="utf-8")
    print(json.dumps({"ok": True, "path": str(OUT), "finalists": len(outcome["finalists"])}, indent=2))


if __name__ == "__main__":
    main()
