"""Stage A fast screen and family kill heuristics."""

from __future__ import annotations

from statistics import median
from typing import Any

from quantara_engine.research.v3.metrics import profit_factor, trade_metrics


def profit_concentration(pnls: list[float]) -> float | None:
    wins = [p for p in pnls if p > 0]
    if not wins:
        return None
    total = sum(wins)
    if total <= 0:
        return None
    return round(max(wins) / total, 4)


def stage_a_screen(
    pnls: list[float],
    risks: list[float],
    *,
    min_trades: int = 25,
) -> dict[str, Any]:
    m = trade_metrics(pnls, risk_usd=risks)
    rs = [p / r for p, r in zip(pnls, risks) if r > 0] if pnls else []
    exp_r = sum(rs) / len(rs) if rs else None
    pf = m.get("pf")
    conc = profit_concentration(pnls)
    reject_reasons: list[str] = []
    if m["trades"] < min_trades:
        reject_reasons.append("insufficient_trades")
    if exp_r is None or exp_r <= 0:
        reject_reasons.append("expectancy_non_positive")
    if pf is None or pf < 1.0:
        reject_reasons.append("pf_below_1")
    if conc is not None and conc > 0.45:
        reject_reasons.append("profit_concentration")
    passed = not reject_reasons
    return {
        **m,
        "expectancy_r": round(exp_r, 4) if exp_r is not None else None,
        "max_dd_r": _max_dd_r(rs),
        "profit_concentration": conc,
        "stage_a": "PASS" if passed else "FAIL",
        "reject_reasons": reject_reasons,
    }


def _max_dd_r(rs: list[float]) -> float | None:
    if not rs:
        return None
    eq = 0.0
    peak = 0.0
    dd = 0.0
    for r in rs:
        eq += r
        peak = max(peak, eq)
        dd = max(dd, peak - eq)
    return round(dd, 4)


def family_kill_update(
    family_rows: list[dict[str, Any]],
    *,
    min_samples: int = 8,
) -> dict[str, Any]:
    if len(family_rows) < min_samples:
        return {"status": "INSUFFICIENT_DATA", "failed": False}

    def _metric(row: dict[str, Any], key: str) -> Any:
        if key in row:
            return row.get(key)
        screen = row.get("screen") or {}
        return screen.get(key)

    pfs = [_metric(r, "pf") for r in family_rows if _metric(r, "pf") is not None]
    exps = [_metric(r, "expectancy_r") for r in family_rows if _metric(r, "expectancy_r") is not None]
    if not pfs or not exps:
        return {"status": "INSUFFICIENT_DATA", "failed": False}
    med_pf = median(pfs)
    med_exp = median(exps)
    positive_pockets = sum(1 for e in exps if e > 0.05)
    failed = med_pf < 0.95 and med_exp < 0 and positive_pockets < 2
    return {
        "status": "FAILED" if failed else "ACTIVE",
        "failed": failed,
        "median_pf": round(med_pf, 4),
        "median_expectancy_r": round(med_exp, 4),
        "positive_pockets": positive_pockets,
        "samples": len(family_rows),
    }
