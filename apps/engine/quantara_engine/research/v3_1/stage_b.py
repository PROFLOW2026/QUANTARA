"""Stage B validation for Stage-A survivors."""

from __future__ import annotations

from typing import Any

from quantara_engine.research.v3.discovery import _chronological_folds, _monte_carlo, _robustness_class
from quantara_engine.research.v3.metrics import profit_factor, trade_metrics
from quantara_engine.research.v3_1.job_plan import V31Job
from quantara_engine.research.v3_1.rule_lab import SimTrade, backtest_family
from quantara_engine.research.v3_1.run_job import _trades_to_pnls


def _benchmark_buy_hold(candles: list, direction: str) -> float | None:
    if len(candles) < 2:
        return None
    s, e = float(candles[0].close), float(candles[-1].close)
    if direction == "short":
        return round((s - e) / s * 100, 4)
    return round((e - s) / s * 100, 4)


def validate_survivor(*, job: V31Job, candles: list) -> dict[str, Any]:
    trades: list[SimTrade] = backtest_family(
        job.family,
        candles=candles,
        direction=job.direction,
        parameters=job.parameters,
    )
    start, end = candles[0].timestamp, candles[-1].timestamp
    folds = _chronological_folds(start, end, n=4)
    fold_pnls: list[list[float]] = []
    for f_start, f_end in folds:
        ft = [t for t in trades if f_start <= t.closed_at < f_end]
        fp, _ = _trades_to_pnls(ft)
        fold_pnls.append(fp)
    from quantara_engine.research.v3.walkforward import summarize_fold_pnls

    wf = summarize_fold_pnls(fold_pnls, pf_fn=profit_factor)
    oos_start, oos_end = folds[-1]
    oos_trades = [t for t in trades if oos_start <= t.closed_at < oos_end]
    op, orisk = _trades_to_pnls(oos_trades)
    rs = [p / r for p, r in zip(op, orisk) if r > 0]
    oos_exp_r = sum(rs) / len(rs) if rs else None
    oos_m = trade_metrics(op, risk_usd=orisk)
    oos_pf = profit_factor(op)
    robust = _robustness_class(
        oos_exp_r=oos_exp_r,
        oos_pf=oos_pf,
        positive_folds=wf["positive_folds"],
        total_folds=wf["folds"],
        oos_trades=oos_m["trades"],
    )
    wins = sum(p for p in op if p > 0)
    conc = (max((p for p in op if p > 0), default=0) / wins) if wins > 0 else None
    worst_fold = min(
        (e for e in wf.get("fold_expectancy_r") or [] if e is not None),
        default=None,
    )
    bh = _benchmark_buy_hold(candles, job.direction)
    return {
        "key": job.key,
        "family": job.family,
        "asset": job.asset,
        "timeframe": job.timeframe,
        "direction": job.direction,
        "parameters": job.parameters,
        "walk_forward": wf,
        "oos": {**oos_m, "expectancy_r": oos_exp_r, "pf": oos_pf},
        "robustness": robust,
        "worst_fold_expectancy_r": worst_fold,
        "profit_concentration": conc,
        "benchmark_buy_hold_return_pct": bh,
        "research_only": oos_m["trades"] < 30,
    }


def portfolio_monte_carlo_and_10k(survivors: list[dict[str, Any]], *, risk_pct: float = 0.25) -> dict[str, Any]:
    rs: list[float] = []
    for row in survivors[:6]:
        exp = (row.get("oos") or {}).get("expectancy_r")
        n = int((row.get("oos") or {}).get("trades") or 0)
        if exp is not None and n > 0:
            rs.extend([float(exp)] * n)
    mc = _monte_carlo(rs)
    start = 10_000.0
    eq = start
    peak = eq
    max_dd = 0.0
    risk_d = start * (risk_pct / 100.0)
    for r in rs:
        eq += r * risk_d
        peak = max(peak, eq)
        max_dd = max(max_dd, peak - eq)
    return {
        "monte_carlo": mc,
        "sim_10k_0_25pct": {
            "starting_equity": start,
            "ending_equity": round(eq, 2),
            "pnl": round(eq - start, 2),
            "return_pct": round((eq / start - 1) * 100, 4),
            "max_dd": round(max_dd, 2),
            "trades": len(rs),
            "capital_utilization": round(len(rs) * risk_pct / 100.0, 4),
        },
    }
