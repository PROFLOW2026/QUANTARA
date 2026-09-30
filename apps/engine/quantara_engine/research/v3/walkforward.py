"""Rolling walk-forward fold utilities (chronological only)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Sequence


@dataclass(frozen=True)
class WalkForwardFold:
    index: int
    train_start: datetime
    train_end: datetime
    test_start: datetime
    test_end: datetime


def rolling_walk_forward_folds(
    *,
    start: datetime,
    end: datetime,
    train_days: int,
    test_days: int,
    step_days: int | None = None,
) -> list[WalkForwardFold]:
    """Non-overlapping test windows; train always ends before test starts."""
    from datetime import timedelta

    step = timedelta(days=step_days or test_days)
    train_span = timedelta(days=train_days)
    test_span = timedelta(days=test_days)
    folds: list[WalkForwardFold] = []
    cursor = start
    idx = 0
    while cursor + train_span + test_span <= end:
        train_end = cursor + train_span
        test_start = train_end
        test_end = test_start + test_span
        folds.append(
            WalkForwardFold(
                index=idx,
                train_start=cursor,
                train_end=train_end,
                test_start=test_start,
                test_end=test_end,
            )
        )
        idx += 1
        cursor += step
    return folds


def summarize_fold_pnls(
    fold_pnls: list[list[float]],
    *,
    pf_fn: Callable[[list[float]], float | None],
) -> dict[str, Any]:
    pfs = [pf_fn(p) for p in fold_pnls if p]
    exps = [sum(p) / len(p) for p in fold_pnls if p]
    positive = sum(1 for e in exps if e > 0)
    return {
        "folds": len(fold_pnls),
        "positive_folds": positive,
        "negative_folds": len(exps) - positive,
        "median_oos_pf": sorted(pfs)[len(pfs) // 2] if pfs else None,
        "worst_oos_pf": min(pfs) if pfs else None,
        "median_oos_expectancy": sorted(exps)[len(exps) // 2] if exps else None,
        "worst_fold_expectancy": min(exps) if exps else None,
    }
