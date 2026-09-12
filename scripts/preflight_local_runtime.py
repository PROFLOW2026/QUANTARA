#!/usr/bin/env python3
"""Pre-start checks: PostgreSQL, quantara_prod, schema, localhost bind, no mock data."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "apps" / "engine"
sys.path.insert(0, str(ENGINE))

from sqlalchemy import text  # noqa: E402

from quantara_engine.core.config import settings  # noqa: E402
from quantara_engine.db.guardrails import (  # noqa: E402
    PRODUCTION_DB_NAME,
    db_name_from_url,
    expected_migration_version,
    validate_postgres_listen_addresses,
    validate_production_market_data_provider,
)
from quantara_engine.db.session import engine  # noqa: E402
from quantara_engine.market_data.polling import STRATEGY_MIN_CANDLES  # noqa: E402


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
        validate_production_market_data_provider(
            settings.database_url.strip(),
            settings.market_data_provider,
        )
        print("PASS  MARKET_DATA_PROVIDER not mock on production")
    except RuntimeError as exc:
        print(f"FAIL  {exc}")
        return 1

    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
            listen = str(conn.execute(text("SHOW listen_addresses")).scalar() or "")
        print(f"PASS  PostgreSQL reachable ({PRODUCTION_DB_NAME})")
        try:
            validate_postgres_listen_addresses(listen)
            print(f"PASS  listen_addresses={listen!r} (localhost only)")
        except RuntimeError as exc:
            print(f"FAIL  {exc}")
            return 1
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

    with engine.connect() as conn:
        mock_rows = int(
            conn.execute(text("SELECT COUNT(*) FROM candles WHERE source = 'mock'")).scalar() or 0
        )
        if mock_rows:
            print(f"FAIL  {mock_rows} mock candle rows remain — run: python scripts/purge_mock_candles.py")
            return 1
        print("PASS  No mock candles in production DB")

        thin = conn.execute(
            text(
                """
                SELECT i.symbol, c.timeframe, COUNT(*) AS n
                FROM instruments i
                JOIN candles c ON c.instrument_id = i.id
                WHERE i.symbol = ANY(:syms)
                GROUP BY i.symbol, c.timeframe
                """
            ),
            {"syms": ["BTCUSD", "ETHUSD", "XAUUSD", "GBPJPY", "NVDA", "TSLA", "AMD", "COIN"]},
        ).fetchall()
        coverage: dict[str, dict[str, int]] = {}
        for symbol, tf, n in thin:
            coverage.setdefault(symbol, {})[tf] = int(n)
        missing_history: list[str] = []
        for sym in ["BTCUSD", "ETHUSD", "XAUUSD", "GBPJPY", "NVDA", "TSLA", "AMD", "COIN"]:
            for tf in ("5m", "15m", "1h"):
                if coverage.get(sym, {}).get(tf, 0) < STRATEGY_MIN_CANDLES:
                    missing_history.append(f"{sym}/{tf}")
        if missing_history:
            print(
                f"FAIL  Insufficient real candle history (<{STRATEGY_MIN_CANDLES}): "
                + ", ".join(missing_history[:8])
                + ("..." if len(missing_history) > 8 else "")
            )
            print("      Run: python scripts/bootstrap_market_data.py")
            return 1
        print(f"PASS  Real candle history >={STRATEGY_MIN_CANDLES} for all active assets/timeframes")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
