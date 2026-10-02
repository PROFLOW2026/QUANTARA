"""Realistic canonical backtest replay for V3.1 ROBUST candidates."""

from __future__ import annotations

import json
import random
import uuid
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from quantara_engine.backtesting.runner import BacktestRun, BacktestRunner
from quantara_engine.domain.types import Instrument, RiskProfile, StrategyInstance, Trade
from quantara_engine.execution.cost_profile import execution_assumptions_for
from quantara_engine.execution.timing import next_execution_timestamp
from quantara_engine.persistence.store import TradingStore
from quantara_engine.research.v3.backtest_lab import trades_to_r_pnls
from quantara_engine.research.v3.constants import V3_RESEARCH_START
from quantara_engine.research.v3.data_quality_gate import build_data_quality_matrix
from quantara_engine.research.v3.discovery import _chronological_folds
from quantara_engine.research.v3.metrics import profit_factor, trade_metrics
from quantara_engine.research.v3_1.rule_lab import SimTrade, backtest_family

ENTRY_MODEL = {
    "signal_timestamp": "completed_bar_i_close (candles[:-1][-1])",
    "execution_eligible_timestamp": "next_execution_timestamp(signal, timeframe) — N+1 bar open",
    "execution_price_source": "CandleProcessor + PaperBrokerAdapter fill at N+1 open with spread/slippage/fees from execution_assumptions_for",
    "execution_assumptions": "execution_assumptions_for(instrument, mark) — canonical cost profile (not flat 8bps rule-lab)",
}


def _risk_profile_025() -> RiskProfile:
    return RiskProfile(
        id="v31-replay",
        slug="v31-replay-0.25",
        name="V3.1 replay 0.25%",
        risk_per_trade_pct=Decimal("0.25"),
        max_open_positions=1,
        max_total_exposure_pct=Decimal("100"),
        daily_loss_limit_pct=Decimal("20"),
        max_drawdown_pct=Decimal("50"),
        parameters={},
    )


def _trade_stats(trades: list[Trade]) -> dict[str, Any]:
    pnls, risks = trades_to_r_pnls(trades)
    rs = [p / r for p, r in zip(pnls, risks) if r > 0]
    m = trade_metrics(pnls, risk_usd=risks)
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    eq = 0.0
    peak = 0.0
    dd = 0.0
    for r in rs:
        eq += r
        peak = max(peak, eq)
        dd = max(dd, peak - eq)
    conc = (max(wins) / sum(wins)) if wins and sum(wins) > 0 else None
    return {
        **m,
        "expectancy_r": round(sum(rs) / len(rs), 4) if rs else None,
        "avg_win_r": round(sum(p / r for p, r in zip(pnls, risks) if p > 0 and r > 0) / max(1, len(wins)), 4)
        if wins
        else None,
        "avg_loss_r": round(sum(p / r for p, r in zip(pnls, risks) if p < 0 and r > 0) / max(1, len(losses)), 4)
        if losses
        else None,
        "max_dd_r": round(dd, 4),
        "profit_concentration": conc,
        "wins": len(wins),
        "losses": len(losses),
    }


def _rule_lab_stats(trades: list[SimTrade]) -> dict[str, Any]:
    pnls = [t.realized_pnl for t in trades]
    risks = [t.risk_amount for t in trades]
    rs = [p / r for p, r in zip(pnls, risks) if r > 0]
    return {
        "trades": len(trades),
        "pf": profit_factor(pnls),
        "expectancy_r": round(sum(rs) / len(rs), 4) if rs else None,
    }


def _data_quality_row(store: TradingStore, symbol: str, timeframe: str) -> dict[str, Any]:
    matrix = build_data_quality_matrix(store).get("matrix") or []
    for row in matrix:
        if row["symbol"] == symbol and row["timeframe"] == timeframe:
            return row
    return {"classification": "UNKNOWN"}


def replay_candidate(
    store: TradingStore,
    *,
    key: str,
    family: str,
    asset: str,
    timeframe: str,
    direction: str,
    parameters: dict[str, Any],
    instrument: Instrument,
    candles: list,
) -> dict[str, Any]:
    dq = _data_quality_row(store, asset, timeframe)
    if dq.get("classification") == "REJECT":
        return {"key": key, "classification": "DATA_REJECT", "data_quality": dq}

    rule_trades = backtest_family(family, candles=candles, direction=direction, parameters=parameters)
    rule_stats = _rule_lab_stats(rule_trades)

    params = {
        **parameters,
        "family": family,
        "trade_direction": direction,
        "symbol": asset,
        "atr_sl": parameters.get("atr_sl", 1.5),
        "atr_tp": parameters.get("atr_tp", 2.5),
    }
    assumptions = execution_assumptions_for(instrument, candles[-1].close)
    instance = StrategyInstance(
        id=str(uuid.uuid4()),
        portfolio_id=str(uuid.uuid4()),
        strategy_version_id=str(uuid.uuid4()),
        strategy_slug="v32-p2-live-sim",
        strategy_version="1.0.0",
        instrument_id=instrument.id,
        timeframe=timeframe,
        risk_profile_id="v31-replay",
        parameter_overrides=params,
        is_active=True,
    )
    run = BacktestRun(
        id=str(uuid.uuid4()),
        strategy_instance=instance,
        instrument=instrument,
        risk_profile=_risk_profile_025(),
        candles=candles,
        initial_capital=Decimal("10000"),
        execution_assumptions=assumptions,
    )
    runner = BacktestRunner()
    runner.run(run, store=None)
    trades: list[Trade] = run.trades or []
    realistic = _trade_stats(trades)
    entry_times = [t.opened_at.isoformat() for t in trades if t.opened_at]

    start, end = candles[0].timestamp, candles[-1].timestamp
    folds = _chronological_folds(start, end, n=4)
    oos_start, oos_end = folds[-1]
    oos_trades = [t for t in trades if t.closed_at and oos_start <= t.closed_at < oos_end]
    oos_stats = _trade_stats(oos_trades)

    fold_stats: list[dict[str, Any]] = []
    pos_folds = 0
    for f_start, f_end in folds:
        ft = [t for t in trades if t.closed_at and f_start <= t.closed_at < f_end]
        st = _trade_stats(ft)
        exp = st.get("expectancy_r")
        if exp is not None and exp > 0:
            pos_folds += 1
        fold_stats.append(st)

    pfs = [f.get("pf") for f in fold_stats if f.get("pf") is not None]
    exps = [f.get("expectancy_r") for f in fold_stats if f.get("expectancy_r") is not None]
    dds = [f.get("max_dd_r") for f in fold_stats if f.get("max_dd_r") is not None]

    oos_exp = oos_stats.get("expectancy_r")
    oos_pf = oos_stats.get("pf")
    oos_n = oos_stats.get("trades") or 0
    conc = realistic.get("profit_concentration")
    survival = "FAIL"
    if oos_exp is not None and oos_exp > 0 and oos_pf is not None and oos_pf >= 1.15 and oos_n >= 30:
        if pos_folds >= 3 and (conc is None or conc <= 0.45):
            survival = "SURVIVES"
        elif oos_pf >= 1.0:
            survival = "DEGRADED_BUT_VALID"
    elif realistic.get("expectancy_r") and realistic["expectancy_r"] > 0 and (realistic.get("pf") or 0) >= 1.05:
        survival = "DEGRADED_BUT_VALID"

    return {
        "key": key,
        "family": family,
        "asset": asset,
        "timeframe": timeframe,
        "direction": direction,
        "parameters": parameters,
        "candidate_id": f"{family}_{parameters}",
        "entry_model": ENTRY_MODEL,
        "data_quality": {
            "classification": dq.get("classification"),
            "expected_bars": dq.get("expected_bars"),
            "actual_bars": dq.get("actual_bars"),
            "missing_bars": dq.get("missing_bars"),
            "completeness_pct": dq.get("completeness_pct"),
        },
        "rule_lab": rule_stats,
        "realistic_full_sample": realistic,
        "realistic_oos": oos_stats,
        "walk_forward_realistic": {
            "folds": len(folds),
            "positive_folds": pos_folds,
            "negative_folds": len(folds) - pos_folds,
            "median_pf": sorted(pfs)[len(pfs) // 2] if pfs else None,
            "worst_pf": min(pfs) if pfs else None,
            "median_expectancy_r": sorted(exps)[len(exps) // 2] if exps else None,
            "worst_expectancy_r": min(exps) if exps else None,
            "median_dd_r": sorted(dds)[len(dds) // 2] if dds else None,
            "worst_dd_r": max(dds) if dds else None,
        },
        "delta_vs_rule_lab": {
            "trade_count": (realistic.get("trades") or 0) - (rule_stats.get("trades") or 0),
            "pf": round((realistic.get("pf") or 0) - (rule_stats.get("pf") or 0), 4)
            if realistic.get("pf") is not None and rule_stats.get("pf") is not None
            else None,
            "expectancy_r": round((realistic.get("expectancy_r") or 0) - (rule_stats.get("expectancy_r") or 0), 4)
            if realistic.get("expectancy_r") is not None
            else None,
        },
        "classification": survival,
        "signal_timestamp_example": next_execution_timestamp(candles[-2].timestamp, timeframe).isoformat()
        if len(candles) > 2
        else None,
        "entry_timestamps": entry_times,
        "portfolio_trade_rows": [
            (t.closed_at, float(t.realized_pnl), float(t.actual_risk_amount or t.target_risk_amount or 0))
            for t in trades
            if t.closed_at and t.realized_pnl is not None
        ],
    }


def overlap_pct(trades_a: list[Trade], trades_b: list[Trade], *, window_minutes: int = 15) -> float:
    if not trades_a or not trades_b:
        return 0.0
    hits = 0
    for ta in trades_a:
        if not ta.opened_at:
            continue
        for tb in trades_b:
            if not tb.opened_at:
                continue
            if abs((ta.opened_at - tb.opened_at).total_seconds()) <= window_minutes * 60:
                hits += 1
                break
    return round(hits / len(trades_a) * 100, 2)


def monte_carlo_r(rs: list[float], *, iterations: int = 5000) -> dict[str, Any]:
    if len(rs) < 5:
        return {}
    random.seed(42)
    totals: list[float] = []
    dds: list[float] = []
    streaks: list[int] = []
    for _ in range(iterations):
        sample = [rs[random.randint(0, len(rs) - 1)] for _ in range(len(rs))]
        eq = 0.0
        peak = 0.0
        dd = 0.0
        streak = 0
        max_streak = 0
        for r in sample:
            eq += r
            peak = max(peak, eq)
            dd = max(dd, peak - eq)
            if r < 0:
                streak += 1
                max_streak = max(max_streak, streak)
            else:
                streak = 0
        totals.append(eq)
        dds.append(dd)
        streaks.append(max_streak)
    totals.sort()
    dds.sort()
    streaks.sort()
    neg = sum(1 for t in totals if t < 0) / iterations
    dd5 = sum(1 for d in dds if d > 0.05) / iterations
    dd10 = sum(1 for d in dds if d > 0.10) / iterations
    return {
        "iterations": iterations,
        "median_return": totals[len(totals) // 2],
        "p10_return": totals[int(len(totals) * 0.1)],
        "p90_return": totals[int(len(totals) * 0.9)],
        "median_dd": dds[len(dds) // 2],
        "p90_dd": dds[int(len(dds) * 0.9)],
        "p95_dd": dds[int(len(dds) * 0.95)],
        "median_losing_streak": streaks[len(streaks) // 2],
        "p95_losing_streak": streaks[int(len(streaks) * 0.95)],
        "probability_negative": round(neg, 4),
        "probability_dd_gt_5pct": round(dd5, 4),
        "probability_dd_gt_10pct": round(dd10, 4),
    }


def portfolio_chronological(trade_rows: list[tuple[datetime, float, float]], *, start: Decimal = Decimal("10000")) -> dict[str, Any]:
    """trade_rows: (closed_at, pnl_usd, risk_usd)."""
    trade_rows = sorted(trade_rows, key=lambda x: x[0])
    eq = float(start)
    peak = eq
    max_dd = 0.0
    rs: list[float] = []
    for _, pnl, risk in trade_rows:
        if risk > 0:
            rs.append(pnl / risk)
        eq += pnl
        peak = max(peak, eq)
        max_dd = max(max_dd, peak - eq)
    pnls = [r for _, p, _ in trade_rows for r in [p]]
    return {
        "starting_equity": float(start),
        "ending_equity": round(eq, 2),
        "pnl": round(eq - float(start), 2),
        "return_pct": round((eq / float(start) - 1) * 100, 4),
        "trades": len(trade_rows),
        "pf": profit_factor(pnls),
        "expectancy_r": round(sum(rs) / len(rs), 4) if rs else None,
        "max_dd_usd": round(max_dd, 2),
        "max_dd_pct": round(max_dd / float(start) * 100, 4),
        "trade_r_outcomes": rs,
    }
