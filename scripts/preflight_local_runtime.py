#!/usr/bin/env python3
"""Pre-start checks: PostgreSQL reachable, quantara_prod selected, schema through 0007."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "apps" / "engine"
sys.path.insert(0, str(ENGINE))

from sqlalchemy import text  # noqa: E402

from quantara_engine.core.config import settings  # noqa: E402
from quantara_engine.db.guardrails import PRODUCTION_DB_NAME, db_name_from_url, expected_migration_version  # noqa: E402
from quantara_engine.db.session import engine  # noqa: E402


def main() -> int:
    print("QUANTARA local preflight")

    if not settings.database_configured:
        print("FAIL  DATABASE_URL not configured")
        return 1

    try:
        settings.validate_runtime_database()
    except RuntimeError as exc:
        print(f"FAIL  {exc}")
        return 1

    db_name = db_name_from_url(settings.database_url)
    if db_name != PRODUCTION_DB_NAME:
        print(f"FAIL  Expected database {PRODUCTION_DB_NAME}, got {db_name!r}")
        return 1

    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        print(f"PASS  PostgreSQL reachable ({PRODUCTION_DB_NAME})")
    except Exception as exc:
        print("FAIL  PostgreSQL not reachable")
        print("      Ensure Windows service postgresql-x64-17 is Running.")
        print(f"      Detail: {exc}")
        return 1

    required_tables = ("paper_runs", "broker_accounts", "portfolios", "strategy_instances")
    with engine.connect() as conn:
        for table in required_tables:
            exists = conn.execute(
                text(
                    """
                    SELECT 1 FROM information_schema.tables
                    WHERE table_schema = 'public' AND table_name = :t
                    """
                ),
                {"t": table},
            ).scalar()
            if not exists:
                print(f"FAIL  Missing table {table} — run npm run db:migrate")
                return 1

    print(f"PASS  Schema current through {expected_migration_version()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
