#!/usr/bin/env python3
"""
Owner-only paper broker reset — DRY-RUN by default.

Pass --execute after owner approval to activate a clean $320k broker account.
"""
from __future__ import annotations

import argparse
import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "engine"))

from sqlalchemy import text  # noqa: E402

from quantara_engine.core.config import settings  # noqa: E402
from quantara_engine.db.session import session_scope  # noqa: E402
from quantara_engine.persistence.store import TradingStore  # noqa: E402

STARTING_CASH = Decimal("320000")
ACCOUNT_SLUG = "quantara_paper_competition"


def _counts(store: TradingStore) -> dict[str, int]:
    session = store.session
    tables = [
        "broker_attribution_ledger",
        "broker_attribution_lots",
        "broker_fills",
        "broker_orders",
        "broker_positions",
        "broker_order_rejections",
    ]
    out: dict[str, int] = {}
    for table in tables:
        try:
            out[table] = int(session.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar() or 0)
        except Exception:
            out[table] = -1
    try:
        out["broker_accounts"] = int(
            session.execute(text("SELECT COUNT(*) FROM broker_accounts")).scalar() or 0
        )
    except Exception:
        out["broker_accounts"] = -1
    try:
        out["open_competition_positions"] = int(
            session.execute(
                text(
                    """
                    SELECT COUNT(*) FROM positions p
                    JOIN strategy_instances si ON si.id = p.strategy_instance_id
                    WHERE p.status = 'open' AND p.backtest_run_id IS NULL
                    """
                )
            ).scalar()
            or 0
        )
    except Exception:
        out["open_competition_positions"] = -1
    try:
        out["pending_intents"] = int(
            session.execute(
                text("SELECT COUNT(*) FROM order_intents WHERE status = 'pending_execution'")
            ).scalar()
            or 0
        )
    except Exception:
        out["pending_intents"] = -1
    return out


def _execute_reset(store: TradingStore) -> None:
    session = store.session
    session.execute(
        text(
            """
            DELETE FROM broker_attribution_ledger
            WHERE broker_fill_id IN (
              SELECT f.id FROM broker_fills f
              JOIN broker_orders o ON o.id = f.broker_order_id
              JOIN broker_accounts a ON a.id = o.broker_account_id
              WHERE a.slug = :slug
            )
            """
        ),
        {"slug": ACCOUNT_SLUG},
    )
    session.execute(
        text(
            """
            DELETE FROM broker_attribution_lots
            WHERE broker_account_id IN (SELECT id FROM broker_accounts WHERE slug = :slug)
            """
        ),
        {"slug": ACCOUNT_SLUG},
    )
    session.execute(
        text(
            """
            DELETE FROM broker_fills WHERE broker_order_id IN (
              SELECT o.id FROM broker_orders o
              JOIN broker_accounts a ON a.id = o.broker_account_id
              WHERE a.slug = :slug
            )
            """
        ),
        {"slug": ACCOUNT_SLUG},
    )
    session.execute(
        text(
            """
            DELETE FROM broker_order_rejections
            WHERE broker_account_id IN (SELECT id FROM broker_accounts WHERE slug = :slug)
            """
        ),
        {"slug": ACCOUNT_SLUG},
    )
    session.execute(
        text(
            """
            DELETE FROM broker_orders
            WHERE broker_account_id IN (SELECT id FROM broker_accounts WHERE slug = :slug)
            """
        ),
        {"slug": ACCOUNT_SLUG},
    )
    session.execute(
        text(
            """
            DELETE FROM broker_positions
            WHERE broker_account_id IN (SELECT id FROM broker_accounts WHERE slug = :slug)
            """
        ),
        {"slug": ACCOUNT_SLUG},
    )
    session.execute(
        text(
            """
            UPDATE order_intents SET status = 'cancelled', updated_at = NOW()
            WHERE status = 'pending_execution'
            """
        )
    )
    session.execute(
        text(
            """
            UPDATE broker_accounts SET
              starting_cash = :cash,
              cash = :cash,
              balance = :cash,
              equity = :cash,
              realized_pnl = 0,
              unrealized_pnl = 0,
              gross_exposure = 0,
              net_exposure = 0,
              initial_margin_used = 0,
              maintenance_margin_required = 0,
              free_margin = :cash,
              available_margin = :cash,
              spot_crypto_cash = :cash,
              is_active = TRUE,
              pending_owner_reset = FALSE,
              account_state = 'active',
              metadata = jsonb_build_object('reset_at', NOW()::text),
              updated_at = NOW()
            WHERE slug = :slug
            """
        ),
        {"slug": ACCOUNT_SLUG, "cash": STARTING_CASH},
    )
    if session.execute(
        text("SELECT 1 FROM broker_accounts WHERE slug = :slug"),
        {"slug": ACCOUNT_SLUG},
    ).scalar() is None:
        session.execute(
            text(
                """
                INSERT INTO broker_accounts (
                  slug, profile_slug, position_mode, starting_cash, cash, balance, equity,
                  realized_pnl, unrealized_pnl, spot_crypto_cash,
                  is_active, pending_owner_reset, account_state
                ) VALUES (
                  :slug, 'quantara_standard_paper', 'netting',
                  :cash, :cash, :cash, :cash, 0, 0, :cash,
                  TRUE, FALSE, 'active'
                )
                """
            ),
            {"slug": ACCOUNT_SLUG, "cash": STARTING_CASH},
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Paper broker competition reset")
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Perform reset (owner approval required)",
    )
    args = parser.parse_args()

    if not settings.database_configured:
        print("DATABASE_URL not configured.")
        sys.exit(1)

    with session_scope() as session:
        store = TradingStore(session)
        before = _counts(store)
        print("QUANTARA Paper Broker Reset")
        print("=" * 40)
        print("BEFORE row counts:")
        for k, v in before.items():
            print(f"  {k}: {v}")

        if not args.execute:
            print("\nDRY-RUN only. Pass --execute after owner approval.")
            print(f"Will initialize {ACCOUNT_SLUG} at ${STARTING_CASH:,.2f} active.")
            return

        _execute_reset(store)
        after = _counts(store)
        print("\nEXECUTED reset.")
        print("AFTER row counts:")
        for k, v in after.items():
            print(f"  {k}: {v}")
        acct = session.execute(
            text(
                """
                SELECT cash, balance, equity, is_active, pending_owner_reset, account_state::text
                FROM broker_accounts WHERE slug = :slug
                """
            ),
            {"slug": ACCOUNT_SLUG},
        ).mappings().first()
        print("\nAccount state:", dict(acct) if acct else "MISSING")


if __name__ == "__main__":
    main()
