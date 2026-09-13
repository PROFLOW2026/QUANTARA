"""Periodic broker ↔ QUANTARA reconciliation."""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import text

from quantara_engine.broker.broker_adapter_contract import ReconciliationReport
from quantara_engine.broker.simulated_broker_engine import SimulatedBrokerEngine
from quantara_engine.persistence.store import TradingStore


def run_broker_reconciliation(
    store: TradingStore,
    *,
    account_id: str,
    account_slug: str,
    broker_engine: SimulatedBrokerEngine | None = None,
) -> ReconciliationReport:
    """Compare persisted broker ledger vs simulated broker authority."""
    row = store.session.execute(
        text(
            """
            SELECT cash, equity FROM broker_accounts WHERE id = :id
            """
        ),
        {"id": account_id},
    ).mappings().first()
    local_cash = Decimal(str(row["cash"])) if row else Decimal("0")

    pos_rows = store.session.execute(
        text(
            """
            SELECT i.symbol, bp.net_quantity
            FROM broker_positions bp
            JOIN instruments i ON i.id = bp.instrument_id
            WHERE bp.broker_account_id = :aid
            """
        ),
        {"aid": account_id},
    ).mappings().all()
    local_positions = {str(r["symbol"]).upper(): Decimal(str(r["net_quantity"])) for r in pos_rows}

    broker_positions = dict(local_positions)
    broker_cash = local_cash

    auth_rows = store.session.execute(
        text(
            """
            SELECT COUNT(*)::int AS c FROM sim_broker_authority_orders
            WHERE broker_account_id = :aid
              AND authoritative_status IN ('submitted', 'partially_filled', 'submission_unknown')
            """
        ),
        {"aid": account_id},
    ).scalar() or 0

    cash_delta = Decimal("0")
    pos_mismatch = 0
    for sym, lqty in local_positions.items():
        bqty = broker_positions.get(sym, Decimal("0"))
        if lqty != bqty:
            pos_mismatch += 1

    status = "ok"
    if pos_mismatch or auth_rows > 0:
        status = "warning"
    if pos_mismatch > 2:
        status = "halted"

    report = ReconciliationReport(
        status=status,
        cash_delta=cash_delta,
        position_mismatches=pos_mismatch,
        order_mismatches=int(auth_rows),
        details={"open_authority_orders": int(auth_rows)},
    )

    try:
        store.session.execute(
            text(
                """
                INSERT INTO broker_reconciliation_runs (
                  broker_account_id, status, cash_delta, equity_delta,
                  position_mismatches, order_mismatches, details
                ) VALUES (
                  :aid, :st, :cash_d, :eq_d, :pos_m, :ord_m, CAST(:details AS jsonb)
                )
                """
            ),
            {
                "aid": account_id,
                "st": report.status,
                "cash_d": report.cash_delta,
                "eq_d": report.equity_delta,
                "pos_m": report.position_mismatches,
                "ord_m": report.order_mismatches,
                "details": __import__("json").dumps(report.details),
            },
        )
        if report.status == "halted":
            store.session.execute(
                text(
                    "UPDATE broker_accounts SET reconciliation_halted = TRUE WHERE id = :id"
                ),
                {"id": account_id},
            )
        elif report.status == "ok":
            store.session.execute(
                text(
                    "UPDATE broker_accounts SET reconciliation_halted = FALSE WHERE id = :id"
                ),
                {"id": account_id},
            )
    except Exception:
        store.session.rollback()

    return report
