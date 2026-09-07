"""Create portfolio snapshots."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from quantara_engine.db.session import session_scope
from quantara_engine.persistence.store import TradingStore

logger = logging.getLogger(__name__)

OWNER_ID = "00000000-0000-0000-0000-000000000001"


def snapshot_job(store: TradingStore | None = None) -> None:
    started_at = datetime.now(timezone.utc)

    def _run(s: TradingStore) -> None:
        portfolio = s.get_or_create_paper_portfolio(owner_id=OWNER_ID)
        state = s.load_portfolio_state(portfolio.id)

        instrument = s.get_instrument_by_symbol("XAUUSD")
        mark = None
        if instrument:
            candles = s.list_candles(instrument.id, "1h", limit=1)
            if candles:
                mark = candles[-1].close

        if mark:
            state.recalculate_equity(mark)

        snap = state.create_snapshot(datetime.now(timezone.utc))
        s.save_snapshot(snap)
        s.update_portfolio(state.portfolio)

        s.update_worker_status(
            "snapshot",
            {"status": "healthy", "last_run": started_at.isoformat()},
        )
        s.save_worker_run(
            run_id=str(uuid.uuid4()),
            worker_name="snapshot",
            started_at=started_at,
            jobs_processed=1,
        )
        logger.info("snapshot job completed for portfolio %s", portfolio.id)

    if store is not None:
        _run(store)
    else:
        with session_scope() as session:
            _run(TradingStore(session))
