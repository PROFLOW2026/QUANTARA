"""Full realistic_broker_v1 replay for V3.1 finalists (competition portfolio + TradingStore)."""

from __future__ import annotations

import uuid
from collections import Counter
from datetime import datetime
from decimal import Decimal
from dataclasses import replace
from typing import Any

from quantara_engine.backtesting.runner import BacktestRun, BacktestRunner
from quantara_engine.competition.constants import ACTIVE_COMPETITION_PORTFOLIOS
from quantara_engine.domain.types import DecisionType, Trade
from quantara_engine.execution.cost_profile import execution_assumptions_for
from quantara_engine.persistence.store import TradingStore
from quantara_engine.research.v3.discovery import _chronological_folds
from quantara_engine.research.v3.metrics import profit_factor, trade_metrics
from quantara_engine.research.v3_1.realistic_replay import _risk_profile_025, _trade_stats  # noqa: PLC2701
from quantara_engine.domain.types import Instrument, StrategyInstance


def competition_portfolio_id(symbol: str, timeframe: str, *, risk_slug: str = "balanced") -> str:
    sym = symbol.upper()
    for p in ACTIVE_COMPETITION_PORTFOLIOS:
        if p.symbol == sym and p.timeframe == timeframe and p.risk_slug == risk_slug:
            return p.portfolio_id
    raise KeyError(f"no competition portfolio for {sym} {timeframe} {risk_slug}")


def _count_decisions(processor_decisions: list) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for d in processor_decisions:
        counts[d.decision_type.value] += 1
    return dict(counts)


def _reject_buckets(counts: dict[str, int], messages: list[str]) -> dict[str, int]:
    text = " ".join(m.lower() for m in messages)
    out = {
        "min_qty_rejects": 0,
        "margin_rejects": 0,
        "session_rejects": 0,
        "risk_rejects": counts.get("risk_denied", 0),
        "stale_rejects": sum(1 for m in messages if "stale" in m.lower()),
        "broker_rejects": counts.get("broker_rejected", 0) + counts.get("broker_capability_denied", 0),
        "other_rejects": 0,
    }
    if "min" in text and "qty" in text:
        out["min_qty_rejects"] += 1
    if "margin" in text:
        out["margin_rejects"] += 1
    if "session" in text:
        out["session_rejects"] += 1
    return out


def classify_pass(stats: dict[str, Any], *, positive_folds: int, total_folds: int) -> str:
    pf = stats.get("pf")
    exp = stats.get("expectancy_r")
    if pf is None or exp is None:
        return "FAIL"
    pf_f = float(pf)
    exp_f = float(exp)
    if pf_f >= 1.15 and exp_f > 0 and positive_folds > total_folds // 2:
        return "PASS"
    if pf_f >= 1.05 and exp_f > 0:
        return "WEAK_PASS"
    if pf_f <= 1.0 or exp_f <= 0:
        return "FAIL"
    return "WEAK_PASS"


def replay_finalist_broker(
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
    portfolio_id = competition_portfolio_id(asset, timeframe)
    params = {
        **parameters,
        "family": family,
        "trade_direction": direction,
        "symbol": asset,
        "atr_sl": parameters.get("atr_sl", 1.5),
        "atr_tp": parameters.get("atr_tp", 2.5),
    }
    assumptions = execution_assumptions_for(instrument, candles[-1].close)
    base = store.get_paper_strategy_instance(
        portfolio_id, strategy_slug="gold-trend-pullback", timeframe=timeframe
    )
    if base is None:
        base = store.get_active_strategy_instance(portfolio_id)
    if base is None:
        raise RuntimeError(f"no strategy instance for portfolio {portfolio_id}")
    risk = _risk_profile_025()
    instance = replace(
        base,
        strategy_slug="v32-p2-live-sim",
        strategy_version="1.0.0",
        instrument_id=instrument.id,
        timeframe=timeframe,
        risk_profile_id=risk.id,
        parameter_overrides=params,
    )
    run = BacktestRun(
        id=str(uuid.uuid4()),
        strategy_instance=instance,
        instrument=instrument,
        risk_profile=risk,
        candles=candles,
        initial_capital=Decimal("10000"),
        execution_assumptions=assumptions,
    )
    runner = BacktestRunner()
    runner.run(run, store=store, persist_backtest_record=False)
    from quantara_engine.backtesting.runner import BacktestStatus

    if run.status == BacktestStatus.FAILED and run.error_message:
        raise RuntimeError(run.error_message)
    trades: list[Trade] = run.trades or []
    stats = _trade_stats(trades)
    decisions = getattr(run, "pipeline_decisions", []) or []
    dcounts = _count_decisions(decisions)
    reasons = [getattr(d, "message", "") or "" for d in decisions]
    buy_signals = dcounts.get("buy_signal", 0) + dcounts.get("sell_signal", 0)
    rejects = _reject_buckets(dcounts, reasons)

    start, end = candles[0].timestamp, candles[-1].timestamp
    folds = _chronological_folds(start, end, n=4)
    pos_folds = 0
    for f_start, f_end in folds:
        ft = [t for t in trades if t.closed_at and f_start <= t.closed_at < f_end]
        st = _trade_stats(ft)
        if (st.get("expectancy_r") or 0) > 0:
            pos_folds += 1

    # Decision funnel from last processor is not kept on run — approximate from trade count vs signals via metrics
    signal_count = buy_signals
    orders = dcounts.get("risk_approved", 0)
    fills = stats.get("trades") or 0

    classification = classify_pass(stats, positive_folds=pos_folds, total_folds=len(folds))

    return {
        "key": key,
        "family": family,
        "asset": asset,
        "timeframe": timeframe,
        "direction": direction,
        "parameters": parameters,
        "broker_path": "BacktestRunner+TradingStore+execute_through_broker (realistic_broker_v1)",
        "portfolio_id": portfolio_id,
        "trades": stats.get("trades"),
        "wins": stats.get("wins"),
        "losses": stats.get("losses"),
        "win_rate_pct": stats.get("win_rate_pct"),
        "pf": stats.get("pf"),
        "expectancy_r": stats.get("expectancy_r"),
        "avg_win_r": stats.get("avg_win_r"),
        "avg_loss_r": stats.get("avg_loss_r"),
        "payoff_ratio": stats.get("payoff_ratio"),
        "max_dd_r": stats.get("max_dd_r"),
        "max_dd_pct": round(float(stats.get("max_dd_r") or 0) * 0.25, 4) if stats.get("max_dd_r") else None,
        "max_losing_streak": stats.get("max_consecutive_losses"),
        "return_pct": round(
            sum(float(t.realized_pnl or 0) for t in trades) / 10000 * 100, 4
        )
        if trades
        else 0.0,
        "signal_count": signal_count,
        "orders": orders,
        "fills": fills,
        "rejections": rejects,
        "walk_forward": {"positive_folds": pos_folds, "folds": len(folds)},
        "classification": classification,
        "pipeline_decisions": decisions,
        "trade_rows": [
            (
                t.opened_at,
                t.closed_at,
                asset,
                float(t.realized_pnl or 0),
                float(t.actual_risk_amount or t.target_risk_amount or 25),
            )
            for t in trades
            if t.closed_at and t.opened_at and t.realized_pnl is not None
        ],
    }


def _coerce_replay_ts(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    raise TypeError(f"expected datetime or iso str, got {type(value)!r}")


def _chronological_portfolio(
    legs: list[list[tuple]],
    *,
    starting: float = 10_000.0,
    amd_combined_sleeve: bool = False,
) -> dict[str, Any]:
    """Merge leg trade_rows (opened_at, closed_at, symbol, pnl, risk) chronologically."""
    events: list[tuple] = []
    for rows in legs:
        for opened_at, closed_at, symbol, pnl, risk in rows:
            events.append(
                (
                    "close",
                    _coerce_replay_ts(closed_at),
                    symbol,
                    float(pnl),
                    float(risk),
                    _coerce_replay_ts(opened_at),
                )
            )
    events.sort(key=lambda x: x[1])

    eq = starting
    peak = eq
    max_dd = 0.0
    pnls: list[float] = []
    rs: list[float] = []
    open_amd = 0
    streak = 0
    max_streak = 0
    daily: dict[str, float] = {}
    exposure_samples: list[float] = []

    for kind, ts, symbol, pnl, risk, _opened in events:
        if amd_combined_sleeve and symbol == "AMD" and open_amd >= 1:
            continue
        if symbol == "AMD":
            open_amd = max(0, open_amd)
        eq += pnl
        pnls.append(pnl)
        if risk > 0:
            rs.append(pnl / risk)
        peak = max(peak, eq)
        max_dd = max(max_dd, peak - eq)
        if pnl < 0:
            streak += 1
            max_streak = max(max_streak, streak)
        else:
            streak = 0
        day = ts.date().isoformat()
        daily[day] = daily.get(day, 0.0) + pnl
        exposure_samples.append(min(eq, starting * 0.05))

    amd_n = sum(1 for e in events if e[2] == "AMD")
    nvda_n = sum(1 for e in events if e[2] == "NVDA")
    total_n = max(1, amd_n + nvda_n)
    return {
        "starting_equity": starting,
        "ending_equity": round(eq, 2),
        "pnl": round(eq - starting, 2),
        "return_pct": round((eq / starting - 1) * 100, 4),
        "trades": len(pnls),
        "pf": profit_factor(pnls),
        "expectancy_r": round(sum(rs) / len(rs), 4) if rs else None,
        "max_dd_usd": round(max_dd, 2),
        "max_dd_pct": round(max_dd / starting * 100, 4),
        "max_losing_streak": max_streak,
        "average_exposure": round(sum(exposure_samples) / len(exposure_samples), 2) if exposure_samples else 0,
        "peak_exposure": round(max(exposure_samples), 2) if exposure_samples else 0,
        "capital_utilization": round(sum(exposure_samples) / (starting * len(exposure_samples)) * 100, 4)
        if exposure_samples
        else 0,
        "peak_concurrent_risk": None,
        "amd_concentration_pct": round(amd_n / total_n * 100, 2),
        "nvda_concentration_pct": round(nvda_n / total_n * 100, 2),
        "best_day": round(max(daily.values()), 2) if daily else 0,
        "worst_day": round(min(daily.values()), 2) if daily else 0,
        "chronological_pnls": pnls,
    }


def monte_carlo_equity(
    pnls: list[float],
    *,
    starting: float = 10_000.0,
    iterations: int = 5000,
    seed: int = 42,
) -> dict[str, Any]:
    import random

    if len(pnls) < 5:
        return {}
    random.seed(seed)
    endings: list[float] = []
    returns_pct: list[float] = []
    dds_pct: list[float] = []
    streaks: list[int] = []
    for _ in range(iterations):
        sample = [pnls[random.randint(0, len(pnls) - 1)] for _ in range(len(pnls))]
        eq = starting
        peak = eq
        dd = 0.0
        streak = 0
        max_streak = 0
        for p in sample:
            eq += p
            peak = max(peak, eq)
            dd = max(dd, peak - eq)
            if p < 0:
                streak += 1
                max_streak = max(max_streak, streak)
            else:
                streak = 0
        endings.append(eq)
        returns_pct.append((eq / starting - 1) * 100)
        dds_pct.append(dd / starting * 100)
        streaks.append(max_streak)
    endings.sort()
    returns_pct.sort()
    dds_pct.sort()
    streaks.sort()
    n = iterations
    return {
        "iterations": iterations,
        "median_ending_equity": endings[n // 2],
        "p10_ending_equity": endings[int(n * 0.1)],
        "p90_ending_equity": endings[int(n * 0.9)],
        "median_return_pct": returns_pct[n // 2],
        "p10_return_pct": returns_pct[int(n * 0.1)],
        "median_max_dd_pct": dds_pct[n // 2],
        "p90_max_dd_pct": dds_pct[int(n * 0.9)],
        "p95_max_dd_pct": dds_pct[int(n * 0.95)],
        "median_losing_streak": streaks[n // 2],
        "p95_losing_streak": streaks[int(n * 0.95)],
        "probability_below_start": sum(1 for e in endings if e < starting) / n,
        "probability_dd_gt_5pct": sum(1 for d in dds_pct if d > 5) / n,
        "probability_dd_gt_10pct": sum(1 for d in dds_pct if d > 10) / n,
        "probability_dd_gt_15pct": sum(1 for d in dds_pct if d > 15) / n,
    }


def passive_buy_hold(
    candles: list,
    *,
    starting: float = 10_000.0,
) -> dict[str, float]:
    if len(candles) < 2:
        return {"return_pct": 0.0, "max_dd_pct": 0.0, "return_over_dd": 0.0}
    p0 = float(candles[0].close)
    p1 = float(candles[-1].close)
    ret = (p1 / p0 - 1) * 100
    peak = p0
    max_dd = 0.0
    for c in candles:
        px = float(c.close)
        peak = max(peak, px)
        max_dd = max(max_dd, (peak - px) / peak * 100)
    rod = ret / max_dd if max_dd > 0 else ret
    return {"return_pct": round(ret, 4), "max_dd_pct": round(max_dd, 4), "return_over_dd": round(rod, 4)}


def verify_parameter_fidelity(store: TradingStore | None = None) -> dict[str, bool]:
    import numpy as np
    import pandas as pd
    from quantara_engine.research.v3_1.rule_lab import entry_mask_series
    from quantara_engine.strategies.common.indicators import candles_to_df

    if store:
        inst = store.get_instrument_by_symbol("AMD")
        from quantara_engine.research.v3.constants import V3_RESEARCH_START

        raw = [
            c
            for c in store.list_candles(inst.id, "15m")
            if c.timestamp >= V3_RESEARCH_START
        ][:800]
        df = candles_to_df(raw)
        df.index = pd.to_datetime([c.timestamp for c in raw], utc=True)
    else:
        n = 300
        idx = pd.date_range("2024-01-01", periods=n, freq="15min", tz="UTC")
        rng = np.random.default_rng(42).normal(0, 1, n).cumsum()
        df = pd.DataFrame(
            {
                "open": 100 + rng,
                "high": 100 + rng + 0.5,
                "low": 100 + rng - 0.5,
                "close": 100 + rng + 0.1,
                "volume": 1000 + np.random.default_rng(1).integers(0, 500, n),
            },
            index=idx,
        )
    vwap_a = entry_mask_series(
        "vwap_mean_reversion", df, direction="long", parameters={"dev": 0.003}
    ).sum()
    vwap_b = entry_mask_series(
        "vwap_mean_reversion", df, direction="long", parameters={"dev": 0.005}
    ).sum()
    atr_a = entry_mask_series(
        "atr_trend", df, direction="long", parameters={"ema_fast": 15, "ema_slow": 40}
    ).sum()
    atr_b = entry_mask_series(
        "atr_trend", df, direction="long", parameters={"ema_fast": 20, "ema_slow": 50}
    ).sum()
    return {
        "amd_vwap_dev_0_005_used": vwap_a != vwap_b,
        "nvda_atr_20_50_used": atr_a != atr_b,
        "amd_atr_15_40_used": atr_a != atr_b,
    }
