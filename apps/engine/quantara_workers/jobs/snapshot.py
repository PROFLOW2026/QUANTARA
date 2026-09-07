"""Create portfolio snapshots for all competition portfolios."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from quantara_engine.db.session import session_scope
from quantara_engine.persistence.store import TradingStore

logger = logging.getLogger(__name__)

OWNER_ID = "00000000-0000-0000-0000-000000000001"


def _snapshot_one(s: TradingStore, portfolio_id: str, mark) -> None:
    state = s.load_portfolio_state(portfolio_id)
    if mark:
        state.recalculate_equity(mark)
    snap = state.create_snapshot(datetime.now(timezone.utc))
    s.save_snapshot(snap)
    s.update_portfolio(state.portfolio)


def snapshot_job(store: TradingStore | None = None) -> None:
    started_at = datetime.now(timezone.utc)

    def _run(s: TradingStore) -> None:
        instrument = s.get_instrument_by_symbol("XAUUSD")
        mark = None
        if instrument:
            candles = s.list_candles(instrument.id, "1h", limit=1)
            if candles:
                mark = candles[-1].close

        entries = s.list_competition_entries()
        portfolio_ids = [e["portfolio"].id for e in entries]
        if not portfolio_ids:
            portfolio = s.get_or_create_paper_portfolio(owner_id=OWNER_ID)
            portfolio_ids = [portfolio.id]

        for portfolio_id in portfolio_ids:
            _snapshot_one(s, portfolio_id, mark)

        s.update_worker_status(
            "snapshot",
            {
                "status": "healthy",
                "last_run": started_at.isoformat(),
                "portfolios_snapshotted": len(portfolio_ids),
            },
        )
        s.save_worker_run(
            run_id=str(uuid.uuid4()),
            worker_name="snapshot",
            started_at=started_at,
            jobs_processed=len(portfolio_ids),
        )
        logger.info("snapshot job completed for %d portfolio(s)", len(portfolio_ids))

    if store is not None:
        _run(store)
    else:
        with session_scope() as session:
            _run(TradingStore(session))
