"""Execute one V3.1 Stage A job."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from quantara_engine.research.v3.discovery import _chronological_folds
from quantara_engine.research.v3.metrics import profit_factor
from quantara_engine.research.v3_1.job_plan import V31Job
from quantara_engine.research.v3_1.rule_lab import SimTrade, backtest_family
from quantara_engine.research.v3_1.screen import stage_a_screen


def _trades_to_pnls(trades: list[SimTrade]) -> tuple[list[float], list[float]]:
    return [t.realized_pnl for t in trades], [t.risk_amount for t in trades]


def run_v31_stage_a_job(*, job: V31Job, candles: list) -> dict[str, Any] | None:
    if len(candles) < 250:
        return None
    trades = backtest_family(
        job.family,
        candles=candles,
        direction=job.direction,
        parameters=job.parameters,
    )
    pnls, risks = _trades_to_pnls(trades)
    screen = stage_a_screen(pnls, risks)
    start, end = candles[0].timestamp, candles[-1].timestamp
    folds = _chronological_folds(start, end, n=4)
    fold_exp: list[float | None] = []
    for f_start, f_end in folds:
        ft = [t for t in trades if f_start <= t.closed_at < f_end]
        fp, fr = _trades_to_pnls(ft)
        rs = [p / r for p, r in zip(fp, fr) if r > 0]
        fold_exp.append(round(sum(rs) / len(rs), 4) if rs else None)
    positive_folds = sum(1 for e in fold_exp if e is not None and e > 0)
    return {
        "key": job.key,
        "candidate_id": f"{job.family}_{job.variant_id}",
        "family": job.family,
        "variant_id": job.variant_id,
        "asset": job.asset,
        "timeframe": job.timeframe,
        "direction": job.direction,
        "parameters": job.parameters,
        "screen": screen,
        "pf": screen.get("pf"),
        "expectancy_r": screen.get("expectancy_r"),
        "trades": screen.get("trades"),
        "walk_forward_preview": {
            "positive_folds": positive_folds,
            "folds": len(folds),
            "fold_expectancy_r": fold_exp,
        },
        "stage_a_pass": screen.get("stage_a") == "PASS",
    }
