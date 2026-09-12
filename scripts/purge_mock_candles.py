#!/usr/bin/env python3
"""Delete mock-source candles from production DB only (preserves all other state)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "apps" / "engine"
sys.path.insert(0, str(ENGINE))

from sqlalchemy import text  # noqa: E402

from quantara_engine.core.config import settings  # noqa: E402
from quantara_engine.db.guardrails import PRODUCTION_DB_NAME, db_name_from_url, is_production_database  # noqa: E402
from quantara_engine.db.session import session_scope  # noqa: E402


def main() -> int:
    if not settings.database_configured:
        print("DATABASE_URL not configured.")
        return 1

    db_name = db_name_from_url(settings.database_url)
    if not is_production_database(settings.database_url):
        print(f"Refusing purge outside {PRODUCTION_DB_NAME} (current: {db_name!r}).")
        return 1

    with session_scope() as session:
        before = int(
            session.execute(text("SELECT COUNT(*) FROM candles WHERE source = 'mock'")).scalar() or 0
        )
        session.execute(text("DELETE FROM candles WHERE source = 'mock'"))
        after = int(
            session.execute(text("SELECT COUNT(*) FROM candles WHERE source = 'mock'")).scalar() or 0
        )

    print(f"Purge mock candles on {PRODUCTION_DB_NAME}")
    print(f"  deleted: {before}")
    print(f"  remaining mock rows: {after}")
    return 0 if after == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
