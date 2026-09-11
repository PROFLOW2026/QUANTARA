"""Load canonical broker account from persisted broker tables."""

from __future__ import annotations

from quantara_engine.broker.execution_service import BrokerExecutionService
from quantara_engine.broker.profile import QUANTARA_STANDARD_PAPER
from quantara_engine.broker.types import BrokerAccountSnapshot, BrokerProfile
from quantara_engine.persistence.store import TradingStore


def build_competition_broker_account(
    store: TradingStore,
    *,
    profile: BrokerProfile | None = None,
) -> BrokerAccountSnapshot:
    """Canonical paper broker account — DB truth, not strategy leg aggregation."""
    profile = profile or QUANTARA_STANDARD_PAPER
    service = BrokerExecutionService(store)
    snapshot = service.load_account_snapshot()
    if snapshot.profile_slug != profile.slug:
        return snapshot
    return snapshot


def list_broker_positions(store: TradingStore) -> list[dict]:
    """Net broker positions from broker_positions table."""
    from sqlalchemy import text

    try:
        rows = store.session.execute(
            text(
                """
                SELECT i.symbol, bp.net_quantity, bp.average_price, bp.mark_price,
                       bp.unrealized_pnl, bp.initial_margin
                FROM broker_positions bp
                JOIN broker_accounts ba ON ba.id = bp.broker_account_id
                JOIN instruments i ON i.id = bp.instrument_id
                WHERE ba.slug = 'quantara_paper_competition'
                ORDER BY i.symbol
                """
            )
        ).mappings().all()
    except Exception:
        return []
    return [
        {
            "symbol": r["symbol"],
            "net_quantity": float(r["net_quantity"]),
            "average_price": float(r["average_price"]),
            "mark_price": float(r["mark_price"]),
            "unrealized_pnl": float(r["unrealized_pnl"]),
            "initial_margin": float(r["initial_margin"]),
        }
        for r in rows
    ]
