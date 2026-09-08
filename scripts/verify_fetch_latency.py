#!/usr/bin/env python3
"""Measure fetch_live_job duration and report pipeline timings."""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "engine"))

from sqlalchemy import text  # noqa: E402

from quantara_engine.competition.constants import ACTIVE_COMPETITION_EXPERIMENT_ID  # noqa: E402
from quantara_engine.db.session import engine, session_scope  # noqa: E402
from quantara_engine.persistence.store import TradingStore  # noqa: E402
from quantara_workers.jobs.fetch_data import fetch_live_job  # noqa: E402


def _btc_latest(conn) -> str | None:
    row = conn.execute(
        text(
            """
            SELECT c.timestamp
            FROM candles c
            JOIN instruments i ON i.id = c.instrument_id
            WHERE i.symbol = 'BTCUSD' AND c.timeframe = '5m'
            ORDER BY c.timestamp DESC
            LIMIT 1
            """
        )
    ).scalar()
    return row.isoformat() if row else None


def _financial(conn) -> dict:
    row = conn.execute(
        text(
            """
            SELECT COUNT(DISTINCT p.id)::int,
                   COALESCE(SUM(p.balance), 0)::float,
                   COALESCE(SUM(p.equity), 0)::float,
                   COALESCE(SUM(p.unrealized_pnl), 0)::float
            FROM portfolios p
            JOIN strategy_instances si ON si.portfolio_id = p.id
            WHERE si.experiment_id = :exp
            """
        ),
        {"exp": ACTIVE_COMPETITION_EXPERIMENT_ID},
    ).one()
    open_pos = conn.execute(text("SELECT COUNT(*)::int FROM positions WHERE status = 'open'")).scalar()
    closed = conn.execute(
        text(
            """
            SELECT COUNT(*)::int, COALESCE(SUM(t.realized_pnl), 0)::float
            FROM trades t
            JOIN strategy_instances si ON si.id = t.strategy_instance_id
            WHERE si.experiment_id = :exp AND t.backtest_run_id IS NULL
            """
        ),
        {"exp": ACTIVE_COMPETITION_EXPERIMENT_ID},
    ).one()
    pending = conn.execute(
        text(
            """
            SELECT COUNT(*)::int FROM order_intents oi
            JOIN strategy_instances si ON si.id = oi.strategy_instance_id
            WHERE si.experiment_id = :exp AND oi.status = 'pending_execution'
            """
        ),
        {"exp": ACTIVE_COMPETITION_EXPERIMENT_ID},
    ).scalar()
    return {
        "active_portfolios": row[0],
        "balance": round(float(row[1]), 2),
        "equity": round(float(row[2]), 2),
        "unrealized": round(float(row[3]), 2),
        "open_positions": int(open_pos or 0),
        "closed_trades": int(closed[0] or 0),
        "realized": round(float(closed[1]), 2),
        "pending_intents": int(pending or 0),
    }


def run_cycles(cycles: int = 3) -> dict:
    report: dict = {"cycles": [], "financial_before": None, "financial_after": None}

    with engine.connect() as conn:
        report["financial_before"] = _financial(conn)

    for i in range(cycles):
        with engine.connect() as conn:
            btc_before = _btc_latest(conn)

        started = time.perf_counter()
        started_at = datetime.now(timezone.utc).isoformat()
        fetch_live_job()
        duration_s = time.perf_counter() - started

        with session_scope() as session:
            store = TradingStore(session)
            worker = store.get_settings_dict().get("worker_status:data_fetcher") or {}

        with engine.connect() as conn:
            btc_after = _btc_latest(conn)

        cycle = {
            "cycle": i + 1,
            "started_at": started_at,
            "duration_s": round(duration_s, 2),
            "duration_ms": round(duration_s * 1000, 1),
            "btc_5m_before": btc_before,
            "btc_5m_after": btc_after,
            "timings_ms": worker.get("timings_ms"),
            "phase": worker.get("phase"),
            "derived": worker.get("derived_candles_upserted"),
            "base": worker.get("candles_upserted"),
        }
        report["cycles"].append(cycle)
        if i < cycles - 1:
            time.sleep(2)

    with engine.connect() as conn:
        report["financial_after"] = _financial(conn)

    return report


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    print(json.dumps(run_cycles(n), indent=2, default=str))
