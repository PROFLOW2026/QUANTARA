#!/usr/bin/env python3
"""Walk-forward IS/OOS for Robots A/B/C — Research competition trades, current params."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "engine"))

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

ROBOTS = {
    "Robot A": "gold-trend-pullback",
    "Robot B": "opening-range-breakout",
    "Robot C": "mean-reversion",
    "Robot D": "volatility-squeeze",
    "Robot E": "momentum-continuation",
}


def db_url() -> str:
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("DATABASE_URL="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise RuntimeError("DATABASE_URL missing")


def pf(pnls: list[float]) -> float | None:
    w = sum(p for p in pnls if p > 0)
    l = sum(p for p in pnls if p < 0)
    return round(w / abs(l), 3) if l else None


def classify(is_exp: float | None, oos_exp: float | None, oos_n: int) -> str:
    if oos_n < 8:
        return "INSUFFICIENT"
    if oos_exp is None or is_exp is None:
        return "INSUFFICIENT"
    if oos_exp > 0 and (is_exp <= 0 or oos_exp >= is_exp * 0.5):
        return "ROBUST"
    if oos_exp > 0:
        return "MIXED"
    return "FAIL"


def main() -> None:
    session = sessionmaker(bind=create_engine(db_url()))()
    rows = session.execute(
        text(
            """
            SELECT s.slug AS strategy_slug, i.symbol, si.timeframe::text AS timeframe,
                   t.realized_pnl::float AS pnl, t.closed_at
            FROM trades t
            JOIN strategy_instances si ON si.id = t.strategy_instance_id
            JOIN strategy_versions sv ON sv.id = si.strategy_version_id
            JOIN strategies s ON s.id = sv.strategy_id
            JOIN instruments i ON i.id = t.instrument_id
            WHERE si.experiment_id IS NOT NULL
              AND t.closed_at IS NOT NULL
              AND s.slug = ANY(:slugs)
            ORDER BY t.closed_at ASC
            """
        ),
        {"slugs": list(ROBOTS.values())},
    ).mappings().all()
    session.close()
    if not rows:
        print(json.dumps({"error": "no research trades"}))
        return
    mid = len(rows) // 2
    split_ts = rows[mid]["closed_at"]
    out: dict = {"split_utc": split_ts.isoformat() if hasattr(split_ts, "isoformat") else str(split_ts)}
    for label, slug in ROBOTS.items():
        subset = [r for r in rows if r["strategy_slug"] == slug]
        is_rows = [r for r in subset if r["closed_at"] < split_ts]
        oos_rows = [r for r in subset if r["closed_at"] >= split_ts]
        is_pnls = [float(r["pnl"]) for r in is_rows]
        oos_pnls = [float(r["pnl"]) for r in oos_rows]
        is_exp = mean(is_pnls) if is_pnls else None
        oos_exp = mean(oos_pnls) if oos_pnls else None
        out[label] = {
            "IS_trades": len(is_pnls),
            "IS_PF": pf(is_pnls),
            "IS_expectancy": round(is_exp, 2) if is_exp is not None else None,
            "OOS_trades": len(oos_pnls),
            "OOS_PF": pf(oos_pnls),
            "OOS_expectancy": round(oos_exp, 2) if oos_exp is not None else None,
            "OOS_max_dd_usd": round(min(0, min(oos_pnls)) if oos_pnls else 0, 2),
            "class": classify(is_exp, oos_exp, len(oos_pnls)),
        }
    path = ROOT / "scripts" / "_tmp_walkforward_abc_out.json"
    path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
