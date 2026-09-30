#!/usr/bin/env python3
"""Limited COIN replacement screen — Research competition, same strategy slugs."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from statistics import mean

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "engine"))

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

CANDIDATES = ["NVDA", "TSLA", "AMD", "ETHUSD", "COIN"]
STRATEGIES = ("gold-trend-pullback", "opening-range-breakout", "mean-reversion")


def db_url() -> str:
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("DATABASE_URL="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise RuntimeError("DATABASE_URL missing")


def pf(pnls: list[float]) -> float | None:
    w = sum(p for p in pnls if p > 0)
    l = sum(p for p in pnls if p < 0)
    return round(w / abs(l), 3) if l else None


def main() -> None:
    session = sessionmaker(bind=create_engine(db_url()))()
    results = []
    for sym in CANDIDATES:
        candles = session.execute(
            text(
                """
                SELECT COUNT(*) FROM candles c
                JOIN instruments i ON i.id = c.instrument_id
                WHERE i.symbol = :sym AND c.timeframe IN ('1m','5m')
                  AND c.timestamp > NOW() - INTERVAL '30 days'
                """
            ),
            {"sym": sym},
        ).scalar()
        trades = session.execute(
            text(
                """
                SELECT t.realized_pnl::float AS pnl
                FROM trades t
                JOIN instruments i ON i.id = t.instrument_id
                JOIN strategy_instances si ON si.id = t.strategy_instance_id
                JOIN strategy_versions sv ON sv.id = si.strategy_version_id
                JOIN strategies s ON s.id = sv.strategy_id
                WHERE i.symbol = :sym AND s.slug = ANY(:slugs)
                  AND si.experiment_id IS NOT NULL AND t.closed_at IS NOT NULL
                """
            ),
            {"sym": sym, "slugs": list(STRATEGIES)},
        ).scalars().all()
        pnls = [float(p) for p in trades]
        min_qty_rejects = session.execute(
            text(
                """
                SELECT COUNT(*) FROM live_sim_allocation_log
                WHERE symbol = :sym
                  AND rejection_reason = 'MIN_QUANTITY_EXCEEDS_RISK_BUDGET'
                  AND created_at > NOW() - INTERVAL '30 days'
                """
            ),
            {"sym": sym},
        ).scalar()
        results.append(
            {
                "symbol": sym,
                "data_quality_1m_5m_bars_30d": int(candles or 0),
                "setup_frequency_trades": len(pnls),
                "PF": pf(pnls),
                "expectancy": round(mean(pnls), 2) if pnls else None,
                "max_dd_usd": round(min(pnls), 2) if pnls else None,
                "live_min_qty_reject_rate_30d": int(min_qty_rejects or 0),
            }
        )
    session.close()
    ranked = sorted(
        [r for r in results if r["symbol"] != "COIN"],
        key=lambda x: (
            -(x["expectancy"] or -999),
            -(x["PF"] or 0),
            x["live_min_qty_reject_rate_30d"],
        ),
    )
    top3 = ranked[:3]
    out = {"candidates": results, "top3": top3}
    (ROOT / "scripts" / "_tmp_coin_replacement_out.json").write_text(
        json.dumps(out, indent=2), encoding="utf-8"
    )
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
