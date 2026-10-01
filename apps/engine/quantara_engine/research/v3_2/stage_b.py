"""Stage B — full realistic_broker_v1 only."""

from __future__ import annotations

from typing import Any

from quantara_engine.research.v3.discovery import _chronological_folds, _robustness_class
from quantara_engine.research.v3_1.broker_replay import (
    _chronological_portfolio,
    monte_carlo_equity,
    replay_finalist_broker,
)
from quantara_engine.research.v3_2.job_plan import V32Job


def validate_survivor_broker(*, job: V32Job, candles: list, store, instrument) -> dict[str, Any]:
    rep = replay_finalist_broker(
        store,
        key=job.key,
        family=job.family,
        asset=job.asset,
        timeframe=job.timeframe,
        direction=job.direction,
        parameters=job.parameters,
        instrument=instrument,
        candles=candles,
    )
    trades = rep.get("trades") or 0
    signals = rep.get("signal_count") or 0
    fills = rep.get("fills") or trades
    fill_rate = round(fills / signals, 4) if signals else 0.0
    start, end = candles[0].timestamp, candles[-1].timestamp
    folds = _chronological_folds(start, end, n=4)
    # OOS = last fold broker metrics approximated from full run trade list timing — use classification fields
    oos_pf = rep.get("pf")
    oos_exp = rep.get("expectancy_r")
    pos_folds = (rep.get("walk_forward") or {}).get("positive_folds", 0)
    robust = _robustness_class(
        oos_exp_r=oos_exp,
        oos_pf=oos_pf,
        positive_folds=pos_folds,
        total_folds=len(folds),
        oos_trades=trades // 4 if trades else 0,
    )
    if trades < 30:
        robust = "RESEARCH_ONLY"
    elif trades >= 50 and oos_pf and oos_pf >= 1.15 and oos_exp and oos_exp > 0:
        robust = "ROBUST"
    elif oos_exp and oos_exp > 0:
        robust = "PROMISING"
    else:
        robust = "FAIL"
    if fill_rate < 0.05 and signals > 100:
        robust = "FAIL"
    return {
        "key": job.key,
        "family": job.family,
        "asset": job.asset,
        "timeframe": job.timeframe,
        "direction": job.direction,
        "parameters": job.parameters,
        "broker_replay": {k: v for k, v in rep.items() if k not in ("trade_rows", "pipeline_decisions")},
        "broker_fill_rate": fill_rate,
        "broker_reject_rate": round(1 - fill_rate, 4) if signals else None,
        "oos": {
            "trades": trades,
            "pf": oos_pf,
            "expectancy_r": oos_exp,
        },
        "robustness": robust,
        "trade_rows": rep.get("trade_rows") or [],
    }


def portfolio_monte_carlo_and_10k(rows: list[dict[str, Any]]) -> dict[str, Any]:
    legs = [r.get("trade_rows") or [] for r in rows]
    port = _chronological_portfolio([leg for leg in legs if leg])
    mc = monte_carlo_equity_usd(port.get("chronological_pnls") or [])
    return {"portfolio_sim_10k_0_25": port, "monte_carlo_5000": mc}


def monte_carlo_equity_usd(pnls: list[float], *, starting: float = 10_000.0, iterations: int = 5000) -> dict:
    raw = monte_carlo_equity(pnls, starting=starting, iterations=iterations)
    if not raw:
        return {}
    return {
        "median_return_pct": raw.get("median_return_pct"),
        "p10_return_pct": raw.get("p10_return_pct"),
        "p90_return_pct": raw.get("p90_return_pct"),
        "median_max_dd_pct": raw.get("median_max_dd_pct"),
        "p95_max_dd_pct": raw.get("p95_max_dd_pct"),
        "p95_losing_streak": raw.get("p95_losing_streak"),
        "probability_below_start": raw.get("probability_below_start"),
        "probability_dd_gt_5pct": raw.get("probability_dd_gt_5pct"),
        "probability_dd_gt_10pct": raw.get("probability_dd_gt_10pct"),
        "probability_dd_gt_15pct": raw.get("probability_dd_gt_15pct"),
    }
