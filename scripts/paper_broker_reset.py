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

from quantara_engine.competition.constants import (  # noqa: E402
    ACTIVE_COMPETITION_EXPERIMENT_ID,
    ACTIVE_COMPETITION_PORTFOLIOS,
    COMPETITION_INITIAL_CAPITAL,
)
from quantara_engine.competition.orb_constants import (  # noqa: E402
    ORB_COMPETITION_EXPERIMENT_ID,
    ORB_COMPETITION_INITIAL_CAPITAL,
    ORB_COMPETITION_PORTFOLIOS,
)
from quantara_engine.core.config import settings  # noqa: E402
from quantara_engine.db.session import session_scope  # noqa: E402
from quantara_engine.persistence.store import TradingStore  # noqa: E402

STARTING_CASH = Decimal("320000")
ACCOUNT_SLUG = "quantara_paper_competition"
REFERENCE_CAPITAL = COMPETITION_INITIAL_CAPITAL


def _competition_experiment_ids() -> tuple[str, ...]:
    return (ACTIVE_COMPETITION_EXPERIMENT_ID, ORB_COMPETITION_EXPERIMENT_ID)


def _competition_portfolio_ids() -> list[str]:
    ids = [p.portfolio_id for p in ACTIVE_COMPETITION_PORTFOLIOS]
    ids.extend(p.portfolio_id for p in ORB_COMPETITION_PORTFOLIOS)
    return ids


def _portfolio_reference_capital(portfolio_id: str) -> Decimal:
    orb_ids = {p.portfolio_id for p in ORB_COMPETITION_PORTFOLIOS}
    return ORB_COMPETITION_INITIAL_CAPITAL if portfolio_id in orb_ids else REFERENCE_CAPITAL


def _counts(store: TradingStore) -> dict[str, int]:
    session = store.session
    exp_ids = _competition_experiment_ids()
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
                    WHERE p.status = 'open' AND p.backtest_run_id IS NULL
                      AND p.strategy_instance_id IN (
                        SELECT id FROM strategy_instances
                        WHERE experiment_id = ANY(CAST(:exp_ids AS uuid[]))
                      )
                    """
                ),
                {"exp_ids": list(exp_ids)},
            ).scalar()
            or 0
        )
    except Exception:
        session.rollback()
        out["open_competition_positions"] = -1
    try:
        out["pending_intents"] = int(
            session.execute(
                text("SELECT COUNT(*) FROM order_intents WHERE status = 'pending_execution'")
            ).scalar()
            or 0
        )
    except Exception:
        session.rollback()
        out["pending_intents"] = -1
    try:
        out["historical_trades_preserved"] = int(
            session.execute(
                text("SELECT COUNT(*) FROM trades WHERE backtest_run_id IS NULL")
            ).scalar()
            or 0
        )
    except Exception:
        session.rollback()
        out["historical_trades_preserved"] = -1
    try:
        out["reference_portfolios"] = len(_competition_portfolio_ids())
    except Exception:
        out["reference_portfolios"] = -1
    return out


def _retire_open_competition_positions(store: TradingStore) -> int:
    result = store.session.execute(
        text(
            """
            UPDATE positions p SET
              status = 'closed',
              closed_at = COALESCE(p.closed_at, NOW()),
              updated_at = NOW()
            FROM strategy_instances si
            WHERE si.id = p.strategy_instance_id
              AND p.status = 'open'
              AND p.backtest_run_id IS NULL
              AND si.experiment_id = ANY(CAST(:exp_ids AS uuid[]))
            RETURNING p.id
            """
        ),
        {"exp_ids": list(_competition_experiment_ids())},
    )
    return len(result.fetchall())


def _reset_reference_portfolios(store: TradingStore) -> int:
    updated = 0
    for pid in _competition_portfolio_ids():
        cap = _portfolio_reference_capital(pid)
        store.session.execute(
            text(
                """
                UPDATE portfolios SET
                  initial_capital = :cap,
                  balance = :cap,
                  equity = :cap,
                  unrealized_pnl = 0,
                  exposure_notional = 0,
                  reserved_capital = 0,
                  peak_equity = :cap,
                  status = 'active',
                  halt_reason = NULL,
                  updated_at = NOW()
                WHERE id = CAST(:id AS uuid)
                """
            ),
            {"id": pid, "cap": cap},
        )
        updated += 1
    return updated


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
            UPDATE order_intents SET status = 'expired'
            WHERE status = 'pending_execution'
            """
        )
    )
    _retire_open_competition_positions(store)
    _reset_reference_portfolios(store)
    session.execute(
        text(
            """
            UPDATE broker_accounts SET
              starting_cash = :cash,
              cash = :cash,
              balance = :cash,
              equity = :cash,
              realized_pnl = 0,
              gross_realized_pnl = 0,
              fees_paid = 0,
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
              is_legacy_simulation = FALSE,
              account_state = 'active',
              metadata = jsonb_build_object(
                'reset_at', NOW()::text,
                'previous_run', 'LEGACY_SIMULATION'
              ),
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
                  realized_pnl, gross_realized_pnl, fees_paid, unrealized_pnl, spot_crypto_cash,
                  is_active, pending_owner_reset, account_state
                ) VALUES (
                  :slug, 'quantara_standard_paper', 'netting',
                  :cash, :cash, :cash, :cash, 0, 0, 0, 0, :cash,
                  TRUE, FALSE, 'active'
                )
                """
            ),
            {"slug": ACCOUNT_SLUG, "cash": STARTING_CASH},
        )


def _print_dry_run_plan(before: dict[str, int]) -> None:
    broker_rows = sum(
        before.get(k, 0)
        for k in (
            "broker_attribution_ledger",
            "broker_attribution_lots",
            "broker_fills",
            "broker_orders",
            "broker_positions",
            "broker_order_rejections",
        )
        if before.get(k, 0) >= 0
    )
    print("\nDRY-RUN plan:")
    print(f"  open strategy positions to retire = {before.get('open_competition_positions', 0)}")
    print(f"  pending intents to cancel = {before.get('pending_intents', 0)}")
    print(f"  broker rows to clear = {broker_rows}")
    print(f"  portfolios to reset/reference = {before.get('reference_portfolios', 0)}")
    print(f"  historical trades preserved = {before.get('historical_trades_preserved', 0)}")
    print("\nDRY-RUN only. Pass --execute after owner approval.")
    print(f"Will initialize {ACCOUNT_SLUG} at ${STARTING_CASH:,.2f} active.")


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
            _print_dry_run_plan(before)
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
        assert after.get("open_competition_positions", -1) == 0, "open competition positions remain"


if __name__ == "__main__":
    main()
