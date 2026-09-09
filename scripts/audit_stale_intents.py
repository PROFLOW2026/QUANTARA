#!/usr/bin/env python3
"""Read-only audit of pending_execution intents; optional --repair expires stale intents."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "engine"))

from sqlalchemy import text  # noqa: E402

from quantara_engine.competition.constants import ACTIVE_COMPETITION_EXPERIMENT_ID  # noqa: E402
from quantara_engine.db.session import engine, session_scope  # noqa: E402
from quantara_engine.market_data.polling import is_bar_complete, timeframe_minutes  # noqa: E402
from quantara_engine.persistence.store import TradingStore  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repair", action="store_true", help="Expire stale pending intents")
    args = parser.parse_args()
    now = datetime.now(timezone.utc)

    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT i.symbol, si.timeframe, rp.slug, oi.status,
                       oi.signal_candle_timestamp, oi.execution_candle_timestamp,
                       oi.created_at, oi.rejection_reason
                FROM order_intents oi
                JOIN strategy_instances si ON si.id = oi.strategy_instance_id
                JOIN instruments i ON i.id = si.instrument_id
                JOIN risk_profiles rp ON rp.id = si.risk_profile_id
                WHERE oi.status = 'pending_execution'
                  AND si.experiment_id = :exp
                ORDER BY i.symbol, oi.created_at
                """
            ),
            {"exp": ACTIVE_COMPETITION_EXPERIMENT_ID},
        ).mappings().all()

    batches: dict[str, dict] = {}
    for row in rows:
        key = f"{row['symbol']}_{row['timeframe']}_{row['signal_candle_timestamp']}"
        batch = batches.setdefault(
            key,
            {
                "asset": row["symbol"],
                "timeframe": row["timeframe"],
                "signal_candle": row["signal_candle_timestamp"].isoformat(),
                "intended_execution_candle": (
                    row["execution_candle_timestamp"].isoformat()
                    if row["execution_candle_timestamp"]
                    else None
                ),
                "created_at": row["created_at"].isoformat(),
                "count": 0,
                "risk_tiers": [],
            },
        )
        batch["count"] += 1
        batch["risk_tiers"].append(row["slug"])
        tf = row["timeframe"]
        exec_ts = row["execution_candle_timestamp"]
        if exec_ts:
            passed = is_bar_complete(exec_ts, tf, now)
        else:
            expected = row["signal_candle_timestamp"]
            passed = is_bar_complete(
                expected.replace(
                    minute=expected.minute,
                )
                + __import__("datetime").timedelta(minutes=timeframe_minutes(tf)),
                tf,
                now,
            )
        batch["execution_opportunity_passed"] = passed
        batch["why_still_pending"] = (
            "execution_window_passed — awaiting repair/expiry"
            if passed
            else "within execution window (unexpected)"
        )

    report = {
        "now_utc": now.isoformat(),
        "pending_total": len(rows),
        "batches": list(batches.values()),
    }

    if args.repair:
        with session_scope() as session:
            store = TradingStore(session)
            expired = store.cancel_stale_pending_intents(ACTIVE_COMPETITION_EXPERIMENT_ID, now)
            session.commit()
            report["expired"] = expired
            report["remaining"] = conn_pending_count()

    print(json.dumps(report, indent=2, default=str))


def conn_pending_count() -> int:
    with engine.connect() as conn:
        return conn.execute(
            text(
                """
                SELECT COUNT(*)::int FROM order_intents oi
                JOIN strategy_instances si ON si.id = oi.strategy_instance_id
                WHERE oi.status = 'pending_execution'
                  AND si.experiment_id = :exp
                """
            ),
            {"exp": ACTIVE_COMPETITION_EXPERIMENT_ID},
        ).scalar() or 0


if __name__ == "__main__":
    main()
