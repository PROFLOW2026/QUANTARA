"""V3.2 Stage A screen — paper metrics + broker-light compatibility."""

from __future__ import annotations

from typing import Any

from quantara_engine.research.v3_1.screen import stage_a_screen


def stage_a_broker_screen(
    pnls: list[float],
    risks: list[float],
    broker: dict[str, Any],
    *,
    min_trades: int = 20,
    min_broker_valid_rate: float = 0.08,
) -> dict[str, Any]:
    base = stage_a_screen(pnls, risks, min_trades=min_trades)
    rejects: list[str] = list(base.get("reject_reasons") or [])
    bvr = float(broker.get("broker_valid_rate") or 0.0)
    if bvr < min_broker_valid_rate:
        rejects.append("broker_valid_rate_too_low")
    if int(broker.get("signals") or 0) >= 30 and int(broker.get("broker_valid") or 0) < 3:
        rejects.append("structurally_untradeable")
    passed = not rejects
    return {
        **base,
        "broker": broker,
        "broker_valid_rate": bvr,
        "broker_reject_rate": broker.get("broker_reject_rate"),
        "stage_a": "PASS" if passed else "FAIL",
        "reject_reasons": rejects,
    }


def family_kill_update(family_rows: list[dict[str, Any]], *, min_samples: int = 6) -> dict[str, Any]:
    from quantara_engine.research.v3_1.screen import family_kill_update as _fk

    base = _fk(family_rows, min_samples=min_samples)
    if base.get("failed"):
        return base
    bvr = [
        (r.get("broker") or {}).get("broker_valid_rate")
        for r in family_rows
        if (r.get("broker") or {}).get("broker_valid_rate") is not None
    ]
    if len(bvr) >= min_samples:
        import statistics

        med_bvr = statistics.median(bvr)
        if med_bvr < 0.05:
            return {
                **base,
                "status": "FAILED",
                "failed": True,
                "reason": "family_broker_valid_rate_median_below_5pct",
                "median_broker_valid_rate": round(med_bvr, 4),
            }
    return base
