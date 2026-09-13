"""Live Sim account initialization and pristine-state repair."""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import text

from quantara_engine.broker.accounts import LIVE_SIM_10K_ACCOUNT_SLUG
from quantara_engine.broker.spot_crypto_cash import initial_spot_crypto_cash_for_account
from quantara_engine.persistence.store import TradingStore


def _is_pristine_for_spot_crypto_repair(row: dict) -> bool:
    if str(row.get("slug")) != LIVE_SIM_10K_ACCOUNT_SLUG:
        return False
    cash = Decimal(str(row.get("cash") or "0"))
    spot = Decimal(str(row.get("spot_crypto_cash") or "0"))
    if spot > 0 or cash <= 0:
        return False
    if Decimal(str(row.get("realized_pnl") or "0")) != 0:
        return False
    if Decimal(str(row.get("gross_realized_pnl") or "0")) != 0:
        return False
    if Decimal(str(row.get("fees_paid") or "0")) != 0:
        return False
    if int(row.get("broker_orders") or 0) > 0:
        return False
    if int(row.get("broker_fills") or 0) > 0:
        return False
    if int(row.get("broker_positions") or 0) > 0:
        return False
    if int(row.get("live_sim_positions") or 0) > 0:
        return False
    return True


def repair_pristine_live_sim_spot_crypto_cash(store: TradingStore) -> dict | None:
    """
    Deterministically repair uninitialized spot_crypto_cash on a zero-fill Live Sim account.

    Returns before/after snapshot when repaired, else None.
    """
    row = store.session.execute(
        text(
            """
            SELECT
              ba.id::text,
              ba.slug,
              ba.cash,
              ba.spot_crypto_cash,
              ba.equity,
              ba.starting_cash,
              ba.realized_pnl,
              ba.gross_realized_pnl,
              ba.fees_paid,
              (SELECT COUNT(*) FROM broker_orders bo WHERE bo.broker_account_id = ba.id) AS broker_orders,
              (SELECT COUNT(*) FROM broker_fills bf
                 JOIN broker_orders bo ON bo.id = bf.broker_order_id
                WHERE bo.broker_account_id = ba.id) AS broker_fills,
              (SELECT COUNT(*) FROM broker_positions bp
                 WHERE bp.broker_account_id = ba.id AND bp.net_quantity <> 0) AS broker_positions,
              (SELECT COUNT(*) FROM live_sim_positions lsp
                 WHERE lsp.broker_account_id = ba.id) AS live_sim_positions
            FROM broker_accounts ba
            WHERE ba.slug = :slug
            """
        ),
        {"slug": LIVE_SIM_10K_ACCOUNT_SLUG},
    ).mappings().first()
    if not row or not _is_pristine_for_spot_crypto_repair(dict(row)):
        return None

    cash = Decimal(str(row["cash"]))
    before = {
        "cash": str(cash),
        "spot_crypto_cash": str(row["spot_crypto_cash"]),
        "equity": str(row["equity"]),
    }
    spot = initial_spot_crypto_cash_for_account(starting_cash=cash)
    store.session.execute(
        text(
            """
            UPDATE broker_accounts
            SET spot_crypto_cash = :spot, updated_at = NOW()
            WHERE id = CAST(:id AS uuid)
              AND spot_crypto_cash = 0
              AND cash = :cash
            """
        ),
        {"id": row["id"], "spot": spot, "cash": cash},
    )
    after = {**before, "spot_crypto_cash": str(spot)}
    return {"before": before, "after": after, "repaired": True}
