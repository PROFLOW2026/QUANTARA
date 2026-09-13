"""Orchestrate reconciliation across all enabled broker accounts in an owner portfolio."""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import text

from quantara_engine.broker.reconciliation_loop import run_broker_reconciliation
from quantara_engine.broker.vendor import BrokerConnectionState
from quantara_engine.owner_portfolio.aggregation import aggregate_owner_portfolio
from quantara_engine.persistence.store import TradingStore


@dataclass
class BrokerReconciliationOutcome:
    broker_account_id: str
    slug: str
    status: str
    halted: bool


@dataclass
class OwnerReconciliationReport:
    owner_slug: str
    global_trustworthy: bool
    global_execution_should_halt: bool
    outcomes: list[BrokerReconciliationOutcome] = field(default_factory=list)


def orchestrate_owner_reconciliation(store: TradingStore, owner_slug: str) -> OwnerReconciliationReport:
    snapshot = aggregate_owner_portfolio(store, slug=owner_slug)
    if not snapshot:
        return OwnerReconciliationReport(
            owner_slug=owner_slug,
            global_trustworthy=True,
            global_execution_should_halt=False,
        )

    outcomes: list[BrokerReconciliationOutcome] = []
    any_halted = False
    all_untrusted = True
    enabled_count = 0

    for slice_row in snapshot.broker_slices:
        if not slice_row.enabled:
            continue
        enabled_count += 1
        report = run_broker_reconciliation(
            store,
            account_id=slice_row.broker_account_id,
            account_slug=slice_row.slug,
        )
        halted = report.status == "halted" or slice_row.reconciliation_halted
        if halted:
            any_halted = True
            store.session.execute(
                text(
                    """
                    UPDATE broker_accounts
                    SET connection_state = CAST(:state AS broker_connection_state),
                        reconciliation_halted = TRUE
                    WHERE id = CAST(:id AS uuid)
                    """
                ),
                {"id": slice_row.broker_account_id, "state": BrokerConnectionState.RECONCILIATION_REQUIRED.value},
            )
        else:
            all_untrusted = False
            store.session.execute(
                text(
                    """
                    UPDATE broker_accounts
                    SET connection_state = CAST(:state AS broker_connection_state)
                    WHERE id = CAST(:id AS uuid)
                      AND reconciliation_halted = FALSE
                    """
                ),
                {"id": slice_row.broker_account_id, "state": BrokerConnectionState.CONNECTED.value},
            )
        outcomes.append(
            BrokerReconciliationOutcome(
                broker_account_id=slice_row.broker_account_id,
                slug=slice_row.slug,
                status=report.status,
                halted=halted,
            )
        )

    global_halt = enabled_count > 0 and all_untrusted
    if global_halt:
        store.session.execute(
            text(
                """
                UPDATE owner_trading_portfolios
                SET global_execution_halted = TRUE, updated_at = NOW()
                WHERE slug = :slug
                """
            ),
            {"slug": owner_slug},
        )
    elif not any_halted:
        store.session.execute(
            text(
                """
                UPDATE owner_trading_portfolios
                SET global_execution_halted = FALSE, updated_at = NOW()
                WHERE slug = :slug
                """
            ),
            {"slug": owner_slug},
        )

    try:
        store.session.commit()
    except Exception:
        store.session.rollback()

    return OwnerReconciliationReport(
        owner_slug=owner_slug,
        global_trustworthy=not global_halt,
        global_execution_should_halt=global_halt,
        outcomes=outcomes,
    )


def is_broker_execution_allowed(store: TradingStore, account_slug: str) -> bool:
    row = store.session.execute(
        text(
            """
            SELECT reconciliation_halted, connection_state::text, global_execution_halted
            FROM broker_accounts ba
            LEFT JOIN portfolio_broker_accounts pba ON pba.broker_account_id = ba.id
            LEFT JOIN owner_trading_portfolios otp ON otp.id = pba.owner_portfolio_id
            WHERE ba.slug = :slug
            """
        ),
        {"slug": account_slug},
    ).mappings().first()
    if not row:
        return True
    if row.get("global_execution_halted"):
        return False
    if row.get("reconciliation_halted"):
        return False
    if row.get("connection_state") in ("HALTED", "DISCONNECTED", "RECONCILIATION_REQUIRED"):
        return False
    return True
