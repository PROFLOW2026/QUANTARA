"""Helpers for real-DB broker integration tests (skip when migration 0006 not applied)."""

from __future__ import annotations

import os
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import text

from quantara_engine.core.config import settings
from quantara_engine.db.session import SessionLocal
from quantara_engine.persistence.store import TradingStore

TEST_ACCOUNT_SLUG = "__test_broker_integration__"
STARTING_CASH = Decimal("320000")


def broker_tables_available() -> bool:
    if not settings.database_configured:
        return False
    session = SessionLocal()
    try:
        session.execute(text("SELECT 1 FROM broker_accounts LIMIT 1"))
        return True
    except Exception:
        return False
    finally:
        session.close()


requires_broker_db = pytest.mark.skipif(
    not broker_tables_available(),
    reason="broker migration 0006 not applied (broker_accounts missing)",
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
            INSERT INTO instruments (id, symbol, asset_class, quote_currency, tick_size, lot_size)
            VALUES (:id, 'NVDA', 'stock', 'USD', 0.01, 1)
            ON CONFLICT DO NOTHING
            """
        ),
        {"id": iid},
    )
    return iid


def setup_active_test_account(store: TradingStore) -> str:
    store.session.execute(
        text("DELETE FROM broker_attribution_ledger WHERE broker_fill_id IN (SELECT id FROM broker_fills WHERE broker_order_id IN (SELECT id FROM broker_orders WHERE broker_account_id IN (SELECT id FROM broker_accounts WHERE slug = :slug)))"),
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
              realized_pnl, spot_crypto_cash, is_active, pending_owner_reset, account_state
            ) VALUES (
              :id, :slug, 'quantara_standard_paper', 'netting',
              :start, :start, :start, :start, 0, :start,
              TRUE, FALSE, 'active'
            )
            """
        ),
        {"id": aid, "slug": TEST_ACCOUNT_SLUG, "start": STARTING_CASH},
    )
    store.session.flush()
    return aid


def read_account_balances(store: TradingStore, account_id: str) -> dict:
    row = store.session.execute(
        text(
            """
            SELECT cash, balance, realized_pnl, spot_crypto_cash, account_state::text
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
        "spot_crypto_cash": Decimal(str(row["spot_crypto_cash"])),
        "account_state": str(row["account_state"]),
    }


def patch_paper_account_slug(monkeypatch):
    import quantara_engine.broker.execution_service as es

    monkeypatch.setattr(es, "PAPER_ACCOUNT_SLUG", TEST_ACCOUNT_SLUG)
