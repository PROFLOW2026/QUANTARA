"""Helpers for real-DB broker integration tests on disposable PostgreSQL."""

from __future__ import annotations

import os
import uuid
from decimal import Decimal
from pathlib import Path

import psycopg2
import pytest
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from quantara_engine.core.config import settings
from quantara_engine.persistence.store import TradingStore

TEST_ACCOUNT_SLUG = "__test_broker_integration__"
STARTING_CASH = Decimal("320000")
BROKER_TEST_DB_NAME = os.environ.get("BROKER_TEST_DB_NAME", "quantara_broker_test")
BROKER_TEST_DATABASE_URL = os.environ.get(
    "BROKER_TEST_DATABASE_URL",
    f"postgresql://quantara:quantara@localhost:5432/{BROKER_TEST_DB_NAME}",
)
BROKER_TEST_ADMIN_URL = os.environ.get(
    "BROKER_TEST_ADMIN_URL",
    "postgresql://postgres:postgres@localhost:5432/postgres",
)
MIGRATIONS_DIR = Path(__file__).resolve().parents[3] / "packages" / "db" / "migrations"

_broker_db_ready = False
_embedded_pg = None


def _admin_dsn() -> str:
    return BROKER_TEST_ADMIN_URL


def _postgres_available(url: str) -> bool:
    try:
        conn = psycopg2.connect(url)
        conn.close()
        return True
    except Exception:
        return False


def _apply_migration_file(conn, path: Path) -> None:
    sql = path.read_text(encoding="utf-8")
    conn.exec_driver_sql(sql)


def _broker_tables_exist(url: str) -> bool:
    try:
        engine = create_engine(url, pool_pre_ping=True)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1 FROM broker_accounts LIMIT 1"))
        return True
    except Exception:
        return False


def _apply_all_migrations(url: str) -> None:
    engine = create_engine(url, pool_pre_ping=True)
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        if "owner_recovery" in path.name:
            continue
        with engine.begin() as conn:
            _apply_migration_file(conn, path)


def _seed_disposable_competition_data(url: str) -> None:
    """Minimal reference seed so reset tests can touch 160 competition portfolios."""
    import subprocess
    import sys

    root = Path(__file__).resolve().parents[3]
    env = os.environ.copy()
    env["DATABASE_URL"] = url
    scripts = (
        "seed.py",
        "seed_8_assets.py",
        "seed_competition.py",
        "seed_orb_strategy.py",
        "seed_orb_competition.py",
    )
    for name in scripts:
        script = root / "scripts" / name
        if not script.exists():
            continue
        subprocess.run(
            [sys.executable, str(script)],
            cwd=str(root),
            check=True,
            env=env,
        )


def _provision_local_database() -> str | None:
    if not _postgres_available(_admin_dsn()):
        return None

    admin = psycopg2.connect(_admin_dsn())
    admin.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    cur = admin.cursor()
    cur.execute(
        "DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'quantara') "
        "THEN CREATE ROLE quantara LOGIN PASSWORD 'quantara'; END IF; END $$;"
    )
    cur.execute(f'DROP DATABASE IF EXISTS "{BROKER_TEST_DB_NAME}" WITH (FORCE)')
    cur.execute(f'CREATE DATABASE "{BROKER_TEST_DB_NAME}" OWNER quantara')
    cur.execute(f'GRANT ALL PRIVILEGES ON DATABASE "{BROKER_TEST_DB_NAME}" TO quantara')
    admin.close()

    _apply_all_migrations(BROKER_TEST_DATABASE_URL)
    _seed_disposable_competition_data(BROKER_TEST_DATABASE_URL)
    return BROKER_TEST_DATABASE_URL


def _provision_embedded_postgresql() -> str | None:
    """Portable temp PostgreSQL via testing.postgresql (no Docker / system install)."""
    global _embedded_pg
    try:
        from testing.postgresql import Postgresql

        _embedded_pg = Postgresql()
        url = _embedded_pg.url()
        _apply_all_migrations(url)
        return url
    except Exception:
        _embedded_pg = None
        return None


def _patch_session_factory(url: str) -> None:
    import quantara_engine.core.config as config_mod
    import quantara_engine.db.session as db_session

    test_engine = create_engine(url, pool_pre_ping=True)
    db_session.engine = test_engine
    db_session.SessionLocal = sessionmaker(bind=test_engine, autocommit=False, autoflush=False)
    db_session.DATABASE_URL = url
    config_mod.settings.database_url = url


def _owner_database_url() -> str | None:
    if not settings.database_configured:
        return None
    return settings.database_url.strip()


def provision_broker_test_database() -> str:
    global _broker_db_ready
    if _broker_db_ready:
        return BROKER_TEST_DATABASE_URL

    owner_url = _owner_database_url()
    if owner_url and BROKER_TEST_DATABASE_URL.strip() == owner_url:
        pytest.fail(
            "BROKER_TEST_DATABASE_URL must not equal Owner DATABASE_URL. "
            "Use disposable local PostgreSQL or a dedicated test database."
        )

    local_url = _provision_local_database()
    if local_url:
        _patch_session_factory(local_url)
        _broker_db_ready = True
        return local_url

    embedded_url = _provision_embedded_postgresql()
    if embedded_url:
        _patch_session_factory(embedded_url)
        _broker_db_ready = True
        return embedded_url

    explicit = os.environ.get("BROKER_TEST_DATABASE_URL")
    if explicit and _broker_tables_exist(explicit):
        if owner_url and explicit.strip() == owner_url:
            pytest.fail("BROKER_TEST_DATABASE_URL must not equal Owner DATABASE_URL.")
        _patch_session_factory(explicit)
        _broker_db_ready = True
        return explicit

    pytest.fail(
        "No safe broker test database. Install PostgreSQL locally, set BROKER_TEST_DATABASE_URL "
        "to a disposable DB, or ensure testing.postgresql can start embedded PostgreSQL binaries."
    )


def _ensure_test_instrument(store: TradingStore) -> str:
    row = store.session.execute(
        text("SELECT id::text FROM instruments WHERE symbol = 'NVDA' LIMIT 1")
    ).scalar()
    if row:
        return str(row)
    iid = str(uuid.uuid4())
    store.session.execute(
        text(
            """
            INSERT INTO instruments (
              id, symbol, name, asset_class, base_currency, quote_currency,
              pip_size, contract_size, price_tick_size, quantity_step, min_quantity
            ) VALUES (
              :id, 'NVDA', 'NVDA', 'stock', 'USD', 'USD',
              0.01, 1, 0.01, 1, 1
            )
            """
        ),
        {"id": iid},
    )
    store.session.flush()
    return iid


def setup_active_test_account(store: TradingStore) -> str:
    store.session.execute(
        text(
            """
            DELETE FROM broker_attribution_ledger
            WHERE broker_fill_id IN (
              SELECT f.id FROM broker_fills f
              JOIN broker_orders o ON o.id = f.broker_order_id
              WHERE o.broker_account_id IN (
                SELECT id FROM broker_accounts WHERE slug = :slug
              )
            )
            """
        ),
        {"slug": TEST_ACCOUNT_SLUG},
    )
    store.session.execute(
        text(
            """
            DELETE FROM broker_attribution_lots
            WHERE broker_account_id IN (SELECT id FROM broker_accounts WHERE slug = :slug)
            """
        ),
        {"slug": TEST_ACCOUNT_SLUG},
    )
    store.session.execute(
        text(
            """
            DELETE FROM broker_fills WHERE broker_order_id IN (
              SELECT id FROM broker_orders WHERE broker_account_id IN (
                SELECT id FROM broker_accounts WHERE slug = :slug
              )
            )
            """
        ),
        {"slug": TEST_ACCOUNT_SLUG},
    )
    store.session.execute(
        text(
            """
            DELETE FROM broker_order_rejections
            WHERE broker_order_id IN (
              SELECT id FROM broker_orders WHERE broker_account_id IN (
                SELECT id FROM broker_accounts WHERE slug = :slug
              )
            )
            """
        ),
        {"slug": TEST_ACCOUNT_SLUG},
    )
    store.session.execute(
        text(
            """
            DELETE FROM broker_orders WHERE broker_account_id IN (
              SELECT id FROM broker_accounts WHERE slug = :slug
            )
            """
        ),
        {"slug": TEST_ACCOUNT_SLUG},
    )
    store.session.execute(
        text(
            """
            DELETE FROM broker_positions WHERE broker_account_id IN (
              SELECT id FROM broker_accounts WHERE slug = :slug
            )
            """
        ),
        {"slug": TEST_ACCOUNT_SLUG},
    )
    store.session.execute(
        text("DELETE FROM broker_accounts WHERE slug = :slug"),
        {"slug": TEST_ACCOUNT_SLUG},
    )
    aid = str(uuid.uuid4())
    store.session.execute(
        text(
            """
            INSERT INTO broker_accounts (
              id, slug, profile_slug, position_mode, starting_cash, cash, balance, equity,
              realized_pnl, gross_realized_pnl, fees_paid, spot_crypto_cash,
              is_active, pending_owner_reset, account_state
            ) VALUES (
              :id, :slug, 'quantara_standard_paper', 'netting',
              :start, :start, :start, :start, 0, 0, 0, :start,
              TRUE, FALSE, 'active'
            )
            """
        ),
        {"id": aid, "slug": TEST_ACCOUNT_SLUG, "start": STARTING_CASH},
    )
    _ensure_test_instrument(store)
    store.session.flush()
    return aid


def setup_inactive_placeholder_account(store: TradingStore) -> str:
    store.session.execute(
        text("DELETE FROM broker_order_rejections WHERE broker_account_id IN (SELECT id FROM broker_accounts WHERE slug = 'quantara_paper_competition')")
    )
    store.session.execute(
        text("DELETE FROM broker_fills WHERE broker_order_id IN (SELECT id FROM broker_orders WHERE broker_account_id IN (SELECT id FROM broker_accounts WHERE slug = 'quantara_paper_competition'))")
    )
    store.session.execute(
        text("DELETE FROM broker_orders WHERE broker_account_id IN (SELECT id FROM broker_accounts WHERE slug = 'quantara_paper_competition')")
    )
    store.session.execute(
        text("DELETE FROM broker_positions WHERE broker_account_id IN (SELECT id FROM broker_accounts WHERE slug = 'quantara_paper_competition')")
    )
    store.session.execute(
        text("DELETE FROM broker_accounts WHERE slug = 'quantara_paper_competition'")
    )
    aid = str(uuid.uuid4())
    store.session.execute(
        text(
            """
            INSERT INTO broker_accounts (
              id, slug, profile_slug, position_mode, starting_cash, cash, balance, equity,
              realized_pnl, gross_realized_pnl, fees_paid, spot_crypto_cash,
              is_active, pending_owner_reset, account_state
            ) VALUES (
              :id, 'quantara_paper_competition', 'quantara_standard_paper', 'netting',
              :start, :start, :start, :start, 0, 0, 0, :start,
              FALSE, TRUE, 'paused'
            )
            """
        ),
        {"id": aid, "start": STARTING_CASH},
    )
    _ensure_test_instrument(store)
    store.session.flush()
    return aid


def read_account_balances(store: TradingStore, account_id: str) -> dict:
    row = store.session.execute(
        text(
            """
            SELECT cash, balance, realized_pnl, gross_realized_pnl, fees_paid,
                   spot_crypto_cash, account_state::text
            FROM broker_accounts WHERE id = :id
            """
        ),
        {"id": account_id},
    ).mappings().first()
    assert row
    return {
        "cash": Decimal(str(row["cash"])),
        "balance": Decimal(str(row["balance"])),
        "realized_pnl": Decimal(str(row["realized_pnl"])),
        "gross_realized_pnl": Decimal(str(row.get("gross_realized_pnl") or row["realized_pnl"])),
        "fees_paid": Decimal(str(row.get("fees_paid") or "0")),
        "spot_crypto_cash": Decimal(str(row["spot_crypto_cash"])),
        "account_state": str(row["account_state"]),
    }


def patch_paper_account_slug(monkeypatch):
    import quantara_engine.broker.execution_service as es

    monkeypatch.setattr(es, "PAPER_ACCOUNT_SLUG", TEST_ACCOUNT_SLUG)
