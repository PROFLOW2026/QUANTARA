#!/usr/bin/env python3
"""Clean known invalid residue and run strategy catch-up before unattended testing."""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "engine"))

from quantara_engine.competition.constants import ACTIVE_COMPETITION_EXPERIMENT_ID  # noqa: E402
from quantara_engine.db.session import session_scope  # noqa: E402
from quantara_engine.persistence.store import TradingStore  # noqa: E402
from quantara_workers.jobs.run_strategy import run_strategy_job  # noqa: E402


def cleanup_residue() -> dict:
    now = datetime.now(timezone.utc)
    report: dict = {}

    with session_scope() as session:
        store = TradingStore(session)
        exp_id = store.get_competition_experiment_id() or ACTIVE_COMPETITION_EXPERIMENT_ID
        started_at = store.get_competition_started_at() or now

        dupes = store.cleanup_duplicate_pending_intents(exp_id)
        stale = store.cancel_stale_pending_intents(exp_id, now)
        invalid_snaps = store.delete_invalid_competition_snapshots(exp_id, started_at)
        session.commit()

        report["duplicate_intents"] = dupes
        report["stale_intents_cancelled"] = stale
        report["invalid_snapshots_deleted"] = invalid_snaps

    return report


def main() -> None:
    print("=== CLEANUP RESIDUE ===")
    cleanup = cleanup_residue()
    print(cleanup)

    print("=== RUN STRATEGY CATCH-UP ===")
    run_strategy_job()
    print("strategy job completed")

    print("=== POST-VERIFY ===")
    sys.path.insert(0, str(ROOT / "scripts"))
    from verify_unattended_readiness import verify_db  # noqa: E402

    report = verify_db()
    print(
        {
            "backlog_total": report.get("backlog_total"),
            "timeframe_execution": report.get("timeframe_execution"),
            "pending_intents": report.get("pending_intents"),
            "financial_reconciliation": report.get("financial_reconciliation"),
            "timeframes": report.get("timeframes"),
        }
    )


if __name__ == "__main__":
    main()
