#!/usr/bin/env python3
"""
Remove Live Sim execution/audit rows created before the owner clean baseline.

Default boundary: active paper run started_at (231d0c57… clean_realistic_baseline).
Use --dry-run to inspect counts only.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "engine"))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from sqlalchemy import text

from quantara_engine.competition.paper_run import get_current_paper_run_id
from quantara_engine.db.session import session_scope
from quantara_engine.persistence.store import TradingStore

LIVE_SIM_SLUG = "live-sim-10k"
EXPECTED_RUN_ID = "231d0c57-e991-4008-914c-aac92ab2bf2c"


def _resolve_boundary(store: TradingStore, explicit: str | None) -> datetime:
    if explicit:
        return datetime.fromisoformat(explicit.replace("Z", "+00:00"))
    run_id = get_current_paper_run_id(store)
    if run_id != EXPECTED_RUN_ID:
        raise SystemExit(f"Unexpected active paper run: {run_id} (expected {EXPECTED_RUN_ID})")
    row = store.session.execute(
        text(
            """
            SELECT COALESCE(started_at, created_at) AS boundary_at, metadata
            FROM paper_runs WHERE id = CAST(:rid AS uuid)
            """
        ),
        {"rid": run_id},
    ).mappings().first()
    if not row or not row["boundary_at"]:
        raise SystemExit("Could not resolve clean baseline boundary from paper_runs")
    return row["boundary_at"]


def _count(session, sql: str, params: dict) -> int:
    return int(session.execute(text(sql), params).scalar() or 0)


def audit(session, account_id: str, boundary: datetime) -> dict:
    params = {"aid": account_id, "boundary": boundary}
    return {
        "boundary": boundary.isoformat(),
        "live_sim_allocation_log": _count(
            session,
            """
            SELECT COUNT(*) FROM live_sim_allocation_log
            WHERE broker_account_id = :aid AND created_at < :boundary
            """,
            params,
        ),
        "live_sim_positions": _count(
            session,
            """
            SELECT COUNT(*) FROM live_sim_positions
            WHERE broker_account_id = :aid AND opened_at < :boundary
            """,
            params,
        ),
        "broker_orders": _count(
            session,
            """
            SELECT COUNT(*) FROM broker_orders
            WHERE broker_account_id = :aid AND created_at < :boundary
            """,
            params,
        ),
        "broker_fills": _count(
            session,
            """
            SELECT COUNT(*) FROM broker_fills f
            JOIN broker_orders o ON o.id = f.broker_order_id
            WHERE o.broker_account_id = :aid AND f.created_at < :boundary
            """,
            params,
        ),
        "broker_positions": _count(
            session,
            """
            SELECT COUNT(*) FROM broker_positions
            WHERE broker_account_id = :aid AND updated_at < :boundary
            """,
            params,
        ),
        "sim_broker_authority_orders": _count(
            session,
            """
            SELECT COUNT(*) FROM sim_broker_authority_orders
            WHERE broker_account_id = :aid AND created_at < :boundary
            """,
            params,
        ),
        "broker_protective_orders": _count(
            session,
            """
            SELECT COUNT(*) FROM broker_protective_orders
            WHERE broker_account_id = :aid AND created_at < :boundary
            """,
            params,
        ),
        "broker_cost_accrual_log": _count(
            session,
            """
            SELECT COUNT(*) FROM broker_cost_accrual_log
            WHERE broker_account_id = :aid AND created_at < :boundary
            """,
            params,
        ),
        "broker_reconciliation_runs": _count(
            session,
            """
            SELECT COUNT(*) FROM broker_reconciliation_runs
            WHERE broker_account_id = :aid AND checked_at < :boundary
            """,
            params,
        ),
        "broker_attribution_lots": _count(
            session,
            """
            SELECT COUNT(*) FROM broker_attribution_lots
            WHERE broker_account_id = :aid AND opened_at < :boundary
            """,
            params,
        ),
        "post_reset_allocation_log": _count(
            session,
            """
            SELECT COUNT(*) FROM live_sim_allocation_log
            WHERE broker_account_id = :aid AND created_at >= :boundary
            """,
            params,
        ),
    }


def purge(session, account_id: str, boundary: datetime) -> dict:
    params = {"aid": account_id, "boundary": boundary}
    deleted: dict[str, int] = {}

    # Child tables first (FK-safe order).
    deleted["broker_fills"] = session.execute(
        text(
            """
            DELETE FROM broker_fills f
            USING broker_orders o
            WHERE f.broker_order_id = o.id
              AND o.broker_account_id = :aid
              AND f.created_at < :boundary
            """
        ),
        params,
    ).rowcount

    deleted["sim_broker_authority_orders"] = session.execute(
        text(
            """
            DELETE FROM sim_broker_authority_orders
            WHERE broker_account_id = :aid AND created_at < :boundary
            """
        ),
        params,
    ).rowcount

    deleted["broker_protective_orders"] = session.execute(
        text(
            """
            DELETE FROM broker_protective_orders
            WHERE broker_account_id = :aid AND created_at < :boundary
            """
        ),
        params,
    ).rowcount

    deleted["broker_attribution_lots"] = session.execute(
        text(
            """
            DELETE FROM broker_attribution_lots
            WHERE broker_account_id = :aid AND opened_at < :boundary
            """
        ),
        params,
    ).rowcount

    deleted["live_sim_positions"] = session.execute(
        text(
            """
            DELETE FROM live_sim_positions
            WHERE broker_account_id = :aid AND opened_at < :boundary
            """
        ),
        params,
    ).rowcount

    deleted["broker_orders"] = session.execute(
        text(
            """
            DELETE FROM broker_orders
            WHERE broker_account_id = :aid AND created_at < :boundary
            """
        ),
        params,
    ).rowcount

    deleted["live_sim_allocation_log"] = session.execute(
        text(
            """
            DELETE FROM live_sim_allocation_log
            WHERE broker_account_id = :aid AND created_at < :boundary
            """
        ),
        params,
    ).rowcount

    deleted["broker_cost_accrual_log"] = session.execute(
        text(
            """
            DELETE FROM broker_cost_accrual_log
            WHERE broker_account_id = :aid AND created_at < :boundary
            """
        ),
        params,
    ).rowcount

    deleted["broker_reconciliation_runs"] = session.execute(
        text(
            """
            DELETE FROM broker_reconciliation_runs
            WHERE broker_account_id = :aid AND checked_at < :boundary
            """
        ),
        params,
    ).rowcount

    # Zero-quantity stale broker_positions from pre-reset era only.
    deleted["broker_positions"] = session.execute(
        text(
            """
            DELETE FROM broker_positions
            WHERE broker_account_id = :aid
              AND updated_at < :boundary
              AND COALESCE(net_quantity, 0) = 0
            """
        ),
        params,
    ).rowcount

    session.execute(
        text(
            """
            UPDATE broker_accounts
            SET account_metadata = COALESCE(account_metadata, '{}'::jsonb)
              || jsonb_build_object('baseline_reset_at', :boundary_iso)
            WHERE id = :aid
            """
        ),
        {"aid": account_id, "boundary_iso": boundary.isoformat()},
    )
    return deleted


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--boundary", help="ISO timestamp override")
    args = parser.parse_args()

    with session_scope() as session:
        store = TradingStore(session)
        account_id = session.execute(
            text("SELECT id::text FROM broker_accounts WHERE slug = :slug"),
            {"slug": LIVE_SIM_SLUG},
        ).scalar()
        if not account_id:
            raise SystemExit(f"Missing broker account {LIVE_SIM_SLUG}")

        boundary = _resolve_boundary(store, args.boundary)
        before = audit(session, account_id, boundary)
        print(json.dumps({"before": before}, indent=2, default=str))

        if not args.execute:
            print("Dry run only — pass --execute to delete pre-reset Live Sim rows.")
            return

        deleted = purge(session, account_id, boundary)
        session.commit()
        after = audit(session, account_id, boundary)
        print(json.dumps({"deleted": deleted, "after": after}, indent=2, default=str))


if __name__ == "__main__":
    main()
