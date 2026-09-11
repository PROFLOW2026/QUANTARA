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
MIGRATIONS_DIR = Path(__file__).resolve().parents[3] / "packages" / "db" / "migrations"

_broker_db_ready = False


def _admin_dsn() -> str:
    url = BROKER_TEST_DATABASE_URL
    if url.startswith("postgresql://"):
        base = url.rsplit("/", 1)[0]
        return f"{base}/postgres"
    return "postgresql://quantara:quantara@localhost:5432/postgres"


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


def _provision_local_database() -> str | None:
    if not _postgres_available(_admin_dsn()):
        return None

    admin = psycopg2.connect(_admin_dsn())
    admin.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    cur = admin.cursor()
    cur.execute(f'DROP DATABASE IF EXISTS "{BROKER_TEST_DB_NAME}"')
    cur.execute(f'CREATE DATABASE "{BROKER_TEST_DB_NAME}"')
    admin.close()

    engine = create_engine(BROKER_TEST_DATABASE_URL, pool_pre_ping=True)
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        with engine.begin() as conn:
            _apply_migration_file(conn, path)
    return BROKER_TEST_DATABASE_URL


def _patch_session_factory(url: str) -> None:
    import quantara_engine.core.config as config_mod
    import quantara_engine.db.session as db_session

    test_engine = create_engine(url, pool_pre_ping=True)
    db_session.engine = test_engine
    db_session.SessionLocal = sessionmaker(bind=test_engine, autocommit=False, autoflush=False)
    db_session.DATABASE_URL = url
    config_mod.settings.database_url = url


def provision_broker_test_database() -> str:
    global _broker_db_ready
    if _broker_db_ready:
        return BROKER_TEST_DATABASE_URL

    local_url = _provision_local_database()
    if local_url:
        _patch_session_factory(local_url)
        _broker_db_ready = True
        return local_url

    if not settings.database_configured:
        pytest.fail("DATABASE_URL not configured for broker integration tests")

    url = settings.database_url
    if not _broker_tables_exist(url):
        pytest.fail(
            "Broker migration 0006 not available. Start local postgres "
            "(docker compose up postgres) or apply 0006 to BROKER_TEST_DATABASE_URL."
        )
    _patch_session_factory(url)
    _broker_db_ready = True
    return url


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
