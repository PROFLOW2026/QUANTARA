"""Create portfolio snapshots for all competition portfolios."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from quantara_engine.db.session import session_scope
from quantara_engine.persistence.store import TradingStore

logger = logging.getLogger(__name__)


def _marks_for_open_positions(s: TradingStore, state) -> dict:
    """Latest completed candle close per open position instrument/timeframe."""
    marks: dict = {}
    instance = s.get_active_strategy_instance(state.portfolio.id)
    default_tf = instance.timeframe if instance else "5m"
    for pos in state.open_positions():
        if pos.instrument_id in marks:
            continue
        candles = s.list_recent_candles(pos.instrument_id, default_tf, limit=1)
        if candles:
            marks[pos.instrument_id] = candles[-1].close
    return marks


def _snapshot_one(s: TradingStore, portfolio_id: str) -> None:
    state = s.load_portfolio_state(portfolio_id)
    marks = _marks_for_open_positions(s, state)
    if marks:
        state.recalculate_equity(marks)
    else:
        state.portfolio.unrealized_pnl = Decimal("0")
        state.portfolio.equity = state.portfolio.balance
        state.portfolio.exposure_notional = Decimal("0")
        state.portfolio.reserved_capital = Decimal("0")
    snap = state.create_snapshot(datetime.now(timezone.utc))
    s.save_snapshot(snap)
    s.update_portfolio(state.portfolio)
    for pos in state.open_positions():
        s.update_open_position_mark(pos.id, pos.current_price, pos.unrealized_pnl)


def snapshot_job(store: TradingStore | None = None) -> None:
    started_at = datetime.now(timezone.utc)

    def _run(s: TradingStore) -> None:
        _, _, entries = s.list_all_competition_entries()
        portfolio_ids = [e["portfolio"].id for e in entries]
        if not portfolio_ids:
            logger.warning("No active competition portfolios — snapshot job skipped")
            return

        for portfolio_id in portfolio_ids:
            _snapshot_one(s, portfolio_id)

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

    try:
        if store is not None:
            _run(store)
        else:
            with session_scope() as session:
                _run(TradingStore(session))
    except Exception as exc:
        logger.exception("snapshot_job failed")
        try:
            with session_scope() as session:
                s = TradingStore(session)
                s.update_worker_status(
                    "snapshot",
                    {
                        "status": "error",
                        "last_run": started_at.isoformat(),
                        "error": str(exc),
                    },
                )
                s.save_worker_run(
                    run_id=str(uuid.uuid4()),
                    worker_name="snapshot",
                    started_at=started_at,
                    status="failed",
                    errors={"message": str(exc)},
                )
        except Exception:
            logger.exception("Failed to persist snapshot error status")
        raise
