#!/usr/bin/env python3
"""READ-ONLY Owner DB broker schema preflight before applying 0006/0007."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps" / "engine"))

from sqlalchemy import text  # noqa: E402

from quantara_engine.core.config import settings  # noqa: E402
from quantara_engine.db.session import session_scope  # noqa: E402


def _exists(session, name: str, kind: str) -> bool:
    row = session.execute(
        text(
            """
            SELECT 1 FROM pg_catalog.pg_class c
            JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public' AND c.relname = :name AND c.relkind = :kind
            """
        ),
        {"name": name, "kind": kind},
    ).scalar()
    return row is not None


def _enum_exists(session, name: str) -> bool:
    return bool(
        session.execute(
            text("SELECT 1 FROM pg_type WHERE typname = :n"),
            {"n": name},
        ).scalar()
    )


def _table_info(session, table: str) -> dict:
    cols = session.execute(
        text(
            """
            SELECT column_name, data_type, is_nullable
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = :t
            ORDER BY ordinal_position
            """
        ),
        {"t": table},
    ).mappings().all()
    constraints = session.execute(
        text(
            """
            SELECT conname, pg_get_constraintdef(oid) AS def
            FROM pg_constraint
            WHERE conrelid = CAST(:t AS regclass)
            """
        ),
        {"t": table},
    ).mappings().all()
    indexes = session.execute(
        text(
            """
            SELECT indexname, indexdef
            FROM pg_indexes
            WHERE schemaname = 'public' AND tablename = :t
            """
        ),
        {"t": table},
    ).mappings().all()
    count = session.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar()
    return {
        "columns": [dict(c) for c in cols],
        "constraints": [dict(c) for c in constraints],
        "indexes": [dict(i) for i in indexes],
        "row_count": int(count or 0),
    }


def main() -> None:
    if not settings.database_configured:
        print("DATABASE_URL not configured.")
        sys.exit(1)

    report: dict = {"enums": {}, "tables": {}, "rows": {}, "residue": []}

    with session_scope() as session:
        for enum_name in ("broker_account_state", "broker_order_status", "position_mode"):
            report["enums"][enum_name] = _enum_exists(session, enum_name)

        broker_tables = [
            "broker_accounts",
            "broker_positions",
            "broker_orders",
            "broker_fills",
            "broker_attribution_lots",
            "broker_attribution_ledger",
            "broker_order_rejections",
            "paper_runs",
        ]
        for table in broker_tables:
            report["tables"][table] = _exists(session, table, "r")
            if report["tables"][table]:
                report[table] = _table_info(session, table)

        for slug in ("quantara_paper_competition", "__test_broker_integration__"):
            if report["tables"].get("broker_accounts"):
                rows = session.execute(
                    text("SELECT * FROM broker_accounts WHERE slug = :slug"),
                    {"slug": slug},
                ).mappings().all()
                report["rows"][slug] = [dict(r) for r in rows]

        if report["rows"].get("__test_broker_integration__"):
            report["residue"].append("__test_broker_integration__ account row")

        test_orders = 0
        if report["tables"].get("broker_orders"):
            test_orders = int(
                session.execute(
                    text(
                        """
                        SELECT COUNT(*) FROM broker_orders o
                        JOIN broker_accounts a ON a.id = o.broker_account_id
                        WHERE a.slug = '__test_broker_integration__'
                        """
                    )
                ).scalar()
                or 0
            )
        if test_orders:
            report["residue"].append(f"__test_broker_integration__ orders={test_orders}")

        has_broker = any(report["tables"].get(t) for t in broker_tables[:7])
        has_paper_runs = report["tables"].get("paper_runs", False)
        if not has_broker:
            status = "CLEAN"
            action = "APPLY EXACT FINAL 0006 then 0007"
        elif report["residue"]:
            status = "PARTIAL_TEST_RESIDUE"
            action = "GENERATE recovery SQL — do not apply piecemeal 0006"
        elif has_broker and report["enums"]["broker_account_state"]:
            status = "ALREADY_CANONICAL" if has_paper_runs else "PARTIAL_TEST_RESIDUE"
            action = (
                "APPLY 0007 only if paper_runs missing; verify schema matches final 0006"
                if has_paper_runs
                else "APPLY 0007 after verifying 0006 canonical"
            )
        else:
            status = "PARTIAL_TEST_RESIDUE"
            action = "GENERATE recovery SQL"

        report["OWNER_DB_BROKER_SCHEMA"] = status
        report["Exact Owner SQL action required"] = action

    print(json.dumps(report, indent=2, default=str))


if __name__ == "__main__":
    main()
