"""Execute one V3.2 Stage A job (broker-light + N+1 paper sim)."""

from __future__ import annotations

from typing import Any

from quantara_engine.market_data.registry import get_asset
from quantara_engine.market_data.sessions import session_allows_entries
from quantara_engine.research.v3.discovery import _chronological_folds
from quantara_engine.research.v3_2.broker_light import broker_light_funnel
from quantara_engine.research.v3_2.job_plan import V32Job
from quantara_engine.research.v3_2.rule_lab import SimTrade, backtest_family
from quantara_engine.research.v3_2.screen import stage_a_broker_screen


def _pnls(trades: list[SimTrade]) -> tuple[list[float], list[float]]:
    return [t.realized_pnl for t in trades], [t.risk_amount for t in trades]


def run_v32_stage_a_job(
    *,
    job: V32Job,
    candles: list,
    store,
    instrument,
) -> dict[str, Any] | None:
    if len(candles) < 250:
        return None
    asset_obj = get_asset(job.asset)
    session_open = (
        (lambda ts: session_allows_entries(asset_obj.trading_sessions or {}, ts)) if asset_obj else None
    )
    trades = backtest_family(
        job.family,
        candles=candles,
        direction=job.direction,
        parameters=job.parameters,
        session_open=session_open,
    )
    pnls, risks = _pnls(trades)
    stride = 3 if job.timeframe == "5m" else 1
    broker = broker_light_funnel(
        store,
        instrument=instrument,
        asset=job.asset,
        candles=candles,
        family=job.family,
        direction=job.direction,
        parameters=job.parameters,
        sample_stride=stride,
    )
    screen = stage_a_broker_screen(pnls, risks, broker)
    start, end = candles[0].timestamp, candles[-1].timestamp
    folds = _chronological_folds(start, end, n=4)
    fold_exp: list[float | None] = []
    for f_start, f_end in folds:
        ft = [t for t in trades if f_start <= t.closed_at < f_end]
        fp, fr = _pnls(ft)
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
        "broker": broker,
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
