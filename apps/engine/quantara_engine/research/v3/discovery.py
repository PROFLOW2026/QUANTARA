"""V3 discovery orchestration — walk-forward on backtest trades."""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from quantara_engine.research.v3.checkpoint import (
    append_result,
    load_completed,
    result_key,
    write_state,
)

from quantara_engine.persistence.store import TradingStore
from quantara_engine.research.v3.backtest_lab import (
    direction_metrics,
    filter_trades_by_window,
    run_candidate_backtest,
    trades_to_r_pnls,
)
from quantara_engine.research.v3.candidates import V3Candidate, frozen_v3_candidates
from quantara_engine.research.v3.constants import V3_RESEARCH_START
from quantara_engine.research.v3.data_quality_gate import build_data_quality_matrix
from quantara_engine.research.v3.metrics import profit_factor, trade_metrics
from quantara_engine.research.v3.walkforward import rolling_walk_forward_folds, summarize_fold_pnls
from quantara_engine.strategies.registry import get_latest

_US_EQUITIES = frozenset({"NVDA", "TSLA", "AMD", "COIN"})


def _skip_backtest(candidate: V3Candidate, symbol: str, timeframe: str) -> bool:
    """ORB uses US 5m; other families skip equity 5m (too slow, MIN_QTY-heavy)."""
    slug = candidate.strategy_slug
    if slug == "opening-range-breakout":
        return timeframe != "5m" or symbol not in _US_EQUITIES
    if symbol in _US_EQUITIES and timeframe == "5m":
        return True
    return False


def _test_pairs(quality: dict[str, Any]) -> list[tuple[str, str]]:
    allowed: set[tuple[str, str]] = set()
    for row in quality.get("matrix", []):
        if row["classification"] == "REJECT":
            continue
        if row["timeframe"] in ("5m", "15m", "1h"):
            allowed.add((row["symbol"], row["timeframe"]))
    preferred = [
        ("BTCUSD", "15m"),
        ("BTCUSD", "5m"),
        ("ETHUSD", "15m"),
        ("ETHUSD", "5m"),
        ("TSLA", "5m"),
        ("NVDA", "15m"),
        ("NVDA", "5m"),
        ("XAUUSD", "1h"),
        ("XAUUSD", "15m"),
        ("GBPJPY", "15m"),
        ("GBPJPY", "1h"),
        ("AMD", "15m"),
        ("AMD", "5m"),
        ("COIN", "15m"),
        ("COIN", "5m"),
    ]
    return [p for p in preferred if p in allowed]


def _chronological_folds(start: datetime, end: datetime, n: int = 4) -> list[tuple[datetime, datetime]]:
    span = (end - start) / n
    return [(start + span * i, start + span * (i + 1)) for i in range(n)]


def _robustness_class(
    *,
    oos_exp_r: float | None,
    oos_pf: float | None,
    positive_folds: int,
    total_folds: int,
    oos_trades: int,
) -> str:
    if oos_trades < 30:
        return "FAIL"
    if oos_exp_r is None or oos_exp_r <= 0:
        return "FAIL"
    if oos_pf is None or oos_pf < 1.15:
        return "FAIL"
    if positive_folds < (total_folds // 2 + 1):
        return "MIXED"
    if oos_pf >= 1.2 and positive_folds >= max(3, total_folds - 1):
        return "ROBUST"
    return "PROMISING"


def _monte_carlo(pnls: list[float], *, iterations: int = 500) -> dict[str, float | None]:
    if len(pnls) < 5:
        return {}
    random.seed(42)
    totals: list[float] = []
    max_dds: list[float] = []
    streaks: list[int] = []
    for _ in range(iterations):
        sample = [pnls[random.randint(0, len(pnls) - 1)] for _ in range(len(pnls))]
        eq = 0.0
        peak = 0.0
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
        totals.append(eq)
        max_dds.append(dd)
        streaks.append(max_streak)
    totals.sort()
    max_dds.sort()
    streaks.sort()
    return {
        "median_return": totals[len(totals) // 2],
        "p10_return": totals[int(len(totals) * 0.1)],
        "p90_return": totals[int(len(totals) * 0.9)],
        "median_dd": max_dds[len(max_dds) // 2],
        "p95_dd": max_dds[int(len(max_dds) * 0.95)],
        "p95_loss_streak": float(streaks[int(len(streaks) * 0.95)]),
    }


def run_v3_discovery(
    store: TradingStore,
    *,
    progress: Callable[[str], None] | None = None,
    checkpoint_path: Path | None = None,
    state_path: Path | None = None,
) -> dict[str, Any]:
    quality = build_data_quality_matrix(store)
    pairs = _test_pairs(quality)
    candidates = frozen_v3_candidates()
    completed = load_completed(checkpoint_path) if checkpoint_path else {}
    results: list[dict[str, Any]] = list(completed.values())
    candle_cache: dict[tuple[str, str], list] = {}

    planned = 0
    for candidate in candidates:
        cls = get_latest(candidate.strategy_slug)
        for symbol, timeframe in pairs:
            if _skip_backtest(candidate, symbol, timeframe):
                continue
            if timeframe not in cls.supported_timeframes():
                continue
            planned += 1

    tested = len(completed)
    skipped = 0
    failed = 0
    for candidate in candidates:
        cls = get_latest(candidate.strategy_slug)
        for symbol, timeframe in pairs:
            if _skip_backtest(candidate, symbol, timeframe):
                continue
            if timeframe not in cls.supported_timeframes():
                continue
            instrument = store.get_instrument_by_symbol(symbol)
            if not instrument:
                continue
            cache_key = (str(instrument.id), timeframe)
            if cache_key not in candle_cache:
                candle_cache[cache_key] = [
                    c
                    for c in store.list_candles(instrument.id, timeframe)
                    if c.timestamp >= V3_RESEARCH_START
                ]
            candles = candle_cache[cache_key]
            if not candles:
                continue
            key = result_key(candidate.candidate_id, symbol, timeframe)
            if key in completed:
                skipped += 1
                continue
            start, end = candles[0].timestamp, candles[-1].timestamp
            if progress:
                progress(
                    f"v3_discovery active={candidate.candidate_id} {symbol} {timeframe} "
                    f"done={tested}/{planned}"
                )
            bt = run_candidate_backtest(
                candidate=candidate,
                instrument=instrument,
                timeframe=timeframe,
                candles=candles,
            )
            if bt.get("error"):
                failed += 1
                tested += 1
                continue
            trades = bt["trades"]
            folds = _chronological_folds(start, end, n=4)
            fold_pnls: list[list[float]] = []
            for f_start, f_end in folds:
                fold_trades = filter_trades_by_window(trades, f_start, f_end)
                pnls, _ = trades_to_r_pnls(fold_trades)
                fold_pnls.append(pnls)

            wf = summarize_fold_pnls(fold_pnls, pf_fn=profit_factor)
            oos_pnls, oos_risks = trades_to_r_pnls(
                filter_trades_by_window(trades, folds[-1][0], folds[-1][1])
            )
            oos_rs = [p / r for p, r in zip(oos_pnls, oos_risks)] if oos_pnls else []
            oos_m = trade_metrics(oos_pnls, risk_usd=oos_risks)
            oos_exp_r = sum(oos_rs) / len(oos_rs) if oos_rs else None
            oos_pf = profit_factor(oos_pnls)
            robust = _robustness_class(
                oos_exp_r=oos_exp_r,
                oos_pf=oos_pf,
                positive_folds=wf["positive_folds"],
                total_folds=wf["folds"],
                oos_trades=oos_m["trades"],
            )
            dirs = direction_metrics(trades)
            tested += 1
            row = {
                "candidate_id": candidate.candidate_id,
                "family": candidate.family,
                "asset": symbol,
                "timeframe": timeframe,
                "parameters": candidate.parameters,
                "walk_forward": wf,
                "oos": {**oos_m, "expectancy_r": oos_exp_r, "pf": oos_pf},
                "long": dirs.get("long"),
                "short": dirs.get("short"),
                "robustness": robust,
                "total_trades": len(trades),
            }
            results.append(row)
            if checkpoint_path:
                append_result(checkpoint_path, row)
                completed[key] = row
            if state_path:
                write_state(
                    state_path,
                    {
                        "backtests_completed": tested,
                        "backtests_total": planned,
                        "last_key": key,
                        "last_candidate_id": candidate.candidate_id,
                        "last_asset": symbol,
                        "last_timeframe": timeframe,
                    },
                )
            if progress and tested % 5 == 0:
                progress(f"v3_discovery progress={tested}/{planned} results={len(results)}")

    passing = [r for r in results if r["robustness"] in ("ROBUST", "PROMISING")]
    passing.sort(
        key=lambda r: (
            r["robustness"] != "ROBUST",
            -(r["oos"].get("expectancy_r") or -999),
            -(r["oos"].get("pf") or 0),
        ),
    )
    top10 = passing[:10] if passing else sorted(
        results,
        key=lambda r: (r["oos"].get("expectancy_r") or -999),
        reverse=True,
    )[:10]

    portfolio_oos_r: list[float] = []
    for r in (passing[:6] if passing else top10[:6]):
        exp = r["oos"].get("expectancy_r")
        n = int(r["oos"].get("trades") or 0)
        if exp is not None and n > 0:
            portfolio_oos_r.extend([float(exp)] * n)
    mc = _monte_carlo(portfolio_oos_r)

    robust_n = len([r for r in results if r["robustness"] == "ROBUST"])
    return {
        "quality": quality,
        "candidates_defined": len(candidates),
        "candidates_tested": len(results),
        "backtests_planned": planned,
        "backtests_completed": tested,
        "backtests_skipped_resume": skipped,
        "backtests_failed": failed,
        "robustness_counts": {
            "ROBUST": robust_n,
            "PROMISING": len([r for r in results if r["robustness"] == "PROMISING"]),
            "MIXED": len([r for r in results if r["robustness"] == "MIXED"]),
            "FAIL": len([r for r in results if r["robustness"] == "FAIL"]),
        },
        "candidates_passing_robustness": robust_n,
        "candidates_passing_promising": len(passing),
        "top10": top10,
        "production_finalists": [r for r in passing if r["robustness"] == "ROBUST"][:6],
        "monte_carlo_best": mc,
        "gate": "FAIL" if not robust_n else "PASS",
        "discovery_complete": tested >= planned,
    }
