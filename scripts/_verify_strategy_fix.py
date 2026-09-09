#!/usr/bin/env python3
"""Verify strategy scheduler fix — read-only except when --run-cycles is passed."""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "engine"))

from sqlalchemy import select, text  # noqa: E402

from quantara_engine.db.session import engine, session_scope  # noqa: E402
from quantara_engine.models.trading import Decision as OrmDecision  # noqa: E402
from quantara_engine.persistence.store import TradingStore, _uuid  # noqa: E402
from quantara_workers.jobs.run_strategy import run_strategy_job, strategy_freshness_summary  # noqa: E402


def snapshot() -> dict:
    now = datetime.now(timezone.utc)
    report: dict = {"now_utc": now.isoformat()}
    with session_scope() as session:
        store = TradingStore(session)
        report["freshness"] = strategy_freshness_summary(store, now)
        entries_5m = [e for e in store.list_competition_entries() if e["instance"].timeframe == "5m"]
        ids = [e["instance"].id for e in entries_5m]
        for sym in ("BTCUSD", "EURUSD", "XAUUSD"):
            inst = store.get_instrument_by_symbol(sym)
            if not inst:
                continue
            latest_candle = store.latest_candle_timestamp(inst.id, "5m")
            latest_dec = session.scalar(
                select(OrmDecision)
                .where(OrmDecision.instrument_id == _uuid(inst.id))
                .order_by(OrmDecision.created_at.desc())
                .limit(1)
            )
            report[sym] = {
                "latest_market_5m": latest_candle.isoformat() if latest_candle else None,
                "latest_decision_created": latest_dec.created_at.isoformat() if latest_dec else None,
                "latest_decision_candle": latest_dec.candle_timestamp.isoformat() if latest_dec else None,
                "latest_decision_type": latest_dec.decision_type.value if latest_dec else None,
            }
            if sym == "BTCUSD" and ids:
                report["btc_backlog"] = store.get_timeframe_execution_status(inst.id, "5m", ids, now)

    with engine.connect() as c:
        runs = c.execute(
            text(
                """
                SELECT started_at, completed_at, jobs_processed, status
                FROM worker_runs
                WHERE worker_name = 'strategy_runner'
                ORDER BY started_at DESC
                LIMIT 8
                """
            )
        ).all()
        fin = c.execute(
            text(
                """
                SELECT COALESCE(SUM(balance), 0)::float,
                       COALESCE(SUM(equity), 0)::float,
                       COALESCE(SUM(unrealized_pnl), 0)::float
                FROM portfolios
                WHERE status = 'active'
                """
            )
        ).one()
        trades = c.execute(
            text("SELECT COUNT(*)::int FROM trades WHERE backtest_run_id IS NULL")
        ).scalar()
    report["recent_runs"] = [
        {
            "start": r[0].isoformat(),
            "finish": r[1].isoformat() if r[1] else None,
            "jobs": r[2],
            "status": str(r[3]),
        }
        for r in runs
    ]
    report["financial"] = {
        "balance": fin[0],
        "equity": fin[1],
        "unrealized": fin[2],
        "trades": int(trades or 0),
    }
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-cycles", type=int, default=0)
    args = parser.parse_args()

    before = snapshot()
    cycles = []
    if args.run_cycles:
        for i in range(args.run_cycles):
            t0 = time.perf_counter()
            run_strategy_job()
            cycles.append(
                {
                    "cycle": i + 1,
                    "duration_sec": round(time.perf_counter() - t0, 1),
                    "finished_at": datetime.now(timezone.utc).isoformat(),
                }
            )
            time.sleep(1)

    after = snapshot()
    print(json.dumps({"before": before, "cycles": cycles, "after": after}, indent=2, default=str))


if __name__ == "__main__":
    main()
