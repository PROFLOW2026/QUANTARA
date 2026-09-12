#!/usr/bin/env python3
"""Smoke verification for local PostgreSQL production cutover (no live runtime)."""

from __future__ import annotations

import subprocess
import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "apps" / "engine"
sys.path.insert(0, str(ENGINE))

from sqlalchemy import text  # noqa: E402

from quantara_engine.competition.constants import (  # noqa: E402
    ACTIVE_COMPETITION_PORTFOLIOS,
    COMPETITION_INITIAL_CAPITAL,
)
from quantara_engine.competition.orb_constants import (  # noqa: E402
    ORB_COMPETITION_INITIAL_CAPITAL,
    ORB_COMPETITION_PORTFOLIOS,
)
from quantara_engine.competition.paper_run import get_current_paper_run_id  # noqa: E402
from quantara_engine.core.config import settings  # noqa: E402
from quantara_engine.db.guardrails import (  # noqa: E402
    BROKER_TEST_DB_NAME,
    PRODUCTION_DB_NAME,
    db_name_from_url,
    is_legacy_supabase_url,
)
from quantara_engine.db.session import engine, session_scope  # noqa: E402
from quantara_engine.market_data.active_universe import list_active_db_symbols  # noqa: E402
from quantara_engine.market_data.polling import STRATEGY_MIN_CANDLES  # noqa: E402
from quantara_engine.persistence.store import TradingStore  # noqa: E402

ACCOUNT_SLUG = "quantara_paper_competition"
EXPECTED_PORTFOLIOS = 160
EXPECTED_ROBOT_A = 120
EXPECTED_ROBOT_B = 40
EXPECTED_BROKER_CASH = Decimal("320000")
MIGRATION_MARKER = "0007_paper_competition_runs"


def _fail(msg: str) -> int:
    print(f"FAIL  {msg}")
    return 1


def _pass(msg: str) -> None:
    print(f"PASS  {msg}")


def main() -> int:
    print("QUANTARA local PostgreSQL runtime verification")
    print("=" * 55)

    if not (ROOT / ".env").exists():
        return _fail("Repo root .env missing")

    if not settings.database_configured:
        return _fail("DATABASE_URL not configured")

    try:
        settings.validate_runtime_database()
    except RuntimeError as exc:
        return _fail(str(exc))

    db_name = db_name_from_url(settings.database_url)
    if db_name != PRODUCTION_DB_NAME:
        return _fail(f"Expected database {PRODUCTION_DB_NAME}, got {db_name!r}")

    if is_legacy_supabase_url(settings.database_url):
        return _fail("DATABASE_URL still points at Supabase")

    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        _pass(f"Database connected ({PRODUCTION_DB_NAME})")
    except Exception as exc:
        return _fail(f"Database connection — {exc}")

    with session_scope() as session:
        has_paper_runs = session.execute(
            text(
                """
                SELECT 1 FROM information_schema.tables
                WHERE table_schema = 'public' AND table_name = 'paper_runs'
                """
            )
        ).scalar()
        has_broker = session.execute(
            text(
                """
                SELECT 1 FROM information_schema.tables
                WHERE table_schema = 'public' AND table_name = 'broker_accounts'
                """
            )
        ).scalar()
        if not has_paper_runs or not has_broker:
            return _fail(f"Schema incomplete — expected migration through {MIGRATION_MARKER}")
        _pass(f"Schema through {MIGRATION_MARKER}")

    with session_scope() as session:
        store = TradingStore(session)
        robot_a = len(ACTIVE_COMPETITION_PORTFOLIOS)
        robot_b = len(ORB_COMPETITION_PORTFOLIOS)
        total = int(session.execute(text("SELECT COUNT(*) FROM portfolios")).scalar() or 0)
        if robot_a != EXPECTED_ROBOT_A:
            return _fail(f"Robot A portfolios = {robot_a}, expected {EXPECTED_ROBOT_A}")
        if robot_b != EXPECTED_ROBOT_B:
            return _fail(f"Robot B portfolios = {robot_b}, expected {EXPECTED_ROBOT_B}")
        if total < EXPECTED_PORTFOLIOS:
            return _fail(f"Total portfolios = {total}, expected >= {EXPECTED_PORTFOLIOS}")
        _pass(f"Portfolios — Robot A={robot_a}, Robot B={robot_b}, total={total}")

        if not store.is_orb_competition_enabled():
            return _fail("orb_competition_enabled=false — run: npm run db:seed:orb:activate")
        robot_a_entries, robot_b_entries, combined_entries = store.list_all_competition_entries()
        if len(robot_a_entries) != EXPECTED_ROBOT_A:
            return _fail(f"Active Robot A instances = {len(robot_a_entries)}, expected {EXPECTED_ROBOT_A}")
        if len(robot_b_entries) != EXPECTED_ROBOT_B:
            return _fail(f"Active Robot B instances = {len(robot_b_entries)}, expected {EXPECTED_ROBOT_B}")
        if len(combined_entries) != EXPECTED_PORTFOLIOS:
            return _fail(f"Active competition entries = {len(combined_entries)}, expected {EXPECTED_PORTFOLIOS}")
        _pass("ORB enabled — 120 + 40 active strategy instances")

        ref_rows = session.execute(
            text(
                """
                SELECT balance FROM portfolios
                WHERE id = ANY(CAST(:ids AS uuid[]))
                """
            ),
            {
                "ids": [p.portfolio_id for p in ACTIVE_COMPETITION_PORTFOLIOS]
                + [p.portfolio_id for p in ORB_COMPETITION_PORTFOLIOS],
            },
        ).fetchall()
        for (balance,) in ref_rows:
            bal = Decimal(str(balance))
            if bal not in (COMPETITION_INITIAL_CAPITAL, ORB_COMPETITION_INITIAL_CAPITAL):
                return _fail(f"Unexpected reference balance {bal}")
        _pass(f"Reference capital — ${COMPETITION_INITIAL_CAPITAL} each")

        acct = session.execute(
            text(
                """
                SELECT cash, balance, equity, realized_pnl, gross_realized_pnl, fees_paid
                FROM broker_accounts WHERE slug = :slug
                """
            ),
            {"slug": ACCOUNT_SLUG},
        ).mappings().first()
        if not acct:
            return _fail(f"Broker account {ACCOUNT_SLUG} missing")
        for field in ("cash", "balance", "equity"):
            if Decimal(str(acct[field])) != EXPECTED_BROKER_CASH:
                return _fail(f"Broker {field} = {acct[field]}, expected {EXPECTED_BROKER_CASH}")
        for field in ("realized_pnl", "gross_realized_pnl", "fees_paid"):
            if Decimal(str(acct[field])) != Decimal("0"):
                return _fail(f"Broker {field} != 0")
        _pass(f"Broker — cash/balance/equity ${EXPECTED_BROKER_CASH:,.0f}")

        for table in ("broker_positions", "broker_orders", "broker_fills", "broker_attribution_lots"):
            count = int(session.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar() or 0)
            if count != 0:
                return _fail(f"{table} = {count}, expected 0")

        active_runs = int(
            session.execute(text("SELECT COUNT(*) FROM paper_runs WHERE status = 'active'")).scalar()
            or 0
        )
        if active_runs != 1:
            return _fail(f"Active paper runs = {active_runs}, expected 1")
        _pass("Active paper run = 1")

        run_id = get_current_paper_run_id(store)
        if not run_id:
            return _fail("No current paper run id")

        open_pos = int(
            session.execute(
                text(
                    "SELECT COUNT(*) FROM positions WHERE status = 'open' AND paper_run_id = CAST(:id AS uuid)"
                ),
                {"id": run_id},
            ).scalar()
            or 0
        )
        trades = int(
            session.execute(
                text("SELECT COUNT(*) FROM trades WHERE paper_run_id = CAST(:id AS uuid)"),
                {"id": run_id},
            ).scalar()
            or 0
        )
        pending = int(
            session.execute(
                text(
                    """
                    SELECT COUNT(*) FROM order_intents
                    WHERE status = 'pending_execution' AND paper_run_id = CAST(:id AS uuid)
                    """
                ),
                {"id": run_id},
            ).scalar()
            or 0
        )
        if open_pos or trades or pending:
            return _fail(
                f"Current run not clean — positions={open_pos}, trades={trades}, pending={pending}"
            )
        _pass("Current run clean — positions=0, trades=0, pending=0")

        symbols = list_active_db_symbols()
        ready = True
        for symbol in symbols:
            inst = store.get_instrument_by_symbol(symbol)
            if not inst:
                return _fail(f"Instrument missing: {symbol}")
            for tf in ("5m", "15m", "1h"):
                n = store.count_candles(inst.id, tf)
                if n < STRATEGY_MIN_CANDLES:
                    ready = False
                    print(f"FAIL  {symbol} {tf}: {n} candles < {STRATEGY_MIN_CANDLES}")
        if not ready:
            return 1
        _pass(f"Market data — {len(symbols)} assets, 5m/15m/1h >= {STRATEGY_MIN_CANDLES}")

    broker_test_url = (
        settings.broker_test_database_url.strip()
        or __import__("os").environ.get("BROKER_TEST_DATABASE_URL", "")
    )
    if broker_test_url:
        test_db = db_name_from_url(broker_test_url)
        if test_db == PRODUCTION_DB_NAME:
            return _fail("BROKER_TEST_DATABASE_URL points at production")
        if test_db == BROKER_TEST_DB_NAME:
            _pass(f"Broker test DB isolated ({BROKER_TEST_DB_NAME})")

    if is_legacy_supabase_url(settings.database_url):
        return _fail("Supabase URL in runtime DATABASE_URL")

    _pass("No Supabase runtime dependency")

    print("=" * 55)
    print("ALL CHECKS PASSED — local PostgreSQL ready for Owner start")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
