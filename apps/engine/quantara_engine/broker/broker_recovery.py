"""Engine/Worker restart recovery — reconcile before resuming execution."""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import text

from quantara_engine.broker.broker_authority import load_open_authority_orders, load_authority_order_by_client_id
from quantara_engine.broker.execution_model import ExecutionModelVersion, parse_execution_model, uses_realistic_broker
from quantara_engine.broker.protective_orders import cancel_protective_orders_for_position
from quantara_engine.broker.reconciliation_loop import run_broker_reconciliation
from quantara_engine.broker.simulated_broker_adapter import adapter_for
from quantara_engine.persistence.store import TradingStore


def recover_broker_on_startup(store: TradingStore, *, account_slug: str) -> dict:
    """
    Recovery sequence after Engine/Worker restart:
    load persisted state → rebuild authority → reconcile → no blind resubmit.
    """
    row = store.session.execute(
        text(
            """
            SELECT id::text, execution_model::text, reconciliation_halted
            FROM broker_accounts WHERE slug = :slug LIMIT 1
            """
        ),
        {"slug": account_slug},
    ).mappings().first()
    if not row:
        return {"status": "no_account"}

    account_id = row["id"]
    model = parse_execution_model(str(row.get("execution_model")))
    if not uses_realistic_broker(model):
        return {"status": "legacy_model", "account_id": account_id}

    adapter = adapter_for(store, account_slug, account_id, model)
    open_auth = load_open_authority_orders(store.session, account_id)
    recovered_orders = 0
    submission_unknown = 0

    for auth in open_auth:
        if auth.get("submission_unknown"):
            submission_unknown += 1
            adapter.recover_submission(str(auth["client_order_id"]))
        recovered_orders += 1

    pending = store.session.execute(
        text(
            """
            SELECT id::text, client_order_id, status::text, submission_unknown
            FROM broker_orders
            WHERE broker_account_id = :aid
              AND status IN ('submitted', 'partially_filled', 'validating', 'accepted')
            """
        ),
        {"aid": account_id},
    ).mappings().all()

    report = run_broker_reconciliation(store, account_id=account_id, account_slug=account_slug)

    positions = store.session.execute(
        text(
            """
            SELECT bp.id::text, COUNT(po.id) AS prot_count
            FROM broker_positions bp
            LEFT JOIN broker_protective_orders po
              ON po.broker_position_id = bp.id AND po.status = 'active'
            WHERE bp.broker_account_id = :aid AND bp.net_quantity != 0
            GROUP BY bp.id
            """
        ),
        {"aid": account_id},
    ).mappings().all()

    return {
        "status": "recovered",
        "account_id": account_id,
        "execution_model": model.value,
        "open_authority_orders": len(open_auth),
        "pending_broker_orders": len(pending),
        "submission_unknown_count": submission_unknown,
        "reconciliation": report.status,
        "reconciliation_halted": bool(row.get("reconciliation_halted")) or report.status == "halted",
        "open_positions": len(positions),
        "recovered_orders": recovered_orders,
    }


def recover_order_by_client_id(
    store: TradingStore,
    *,
    account_slug: str,
    client_order_id: str,
) -> dict | None:
    row = store.session.execute(
        text("SELECT id::text, execution_model::text FROM broker_accounts WHERE slug = :slug"),
        {"slug": account_slug},
    ).mappings().first()
    if not row:
        return None
    model = parse_execution_model(str(row.get("execution_model")))
    adapter = adapter_for(store, account_slug, row["id"], model)
    result = adapter.recover_submission(client_order_id)
    auth = load_authority_order_by_client_id(store.session, row["id"], client_order_id)
    return {
        "recovered": result is not None,
        "submission_unknown": bool(auth.get("submission_unknown")) if auth else False,
        "authority_status": auth.get("authoritative_status") if auth else None,
    }
