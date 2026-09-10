"""Create portfolio snapshots for all competition portfolios."""

from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy.exc import OperationalError

from quantara_engine.db.session import session_scope
from quantara_engine.persistence.batch_summary import (
    batch_latest_candle_closes,
    batch_open_positions_by_portfolio,
)
from quantara_engine.persistence.store import TradingStore
from quantara_engine.portfolio.service import PortfolioState

logger = logging.getLogger(__name__)

MAX_DEADLOCK_RETRIES = 3
DEADLOCK_RETRY_BASE_SECONDS = 0.25


def _batch_marks(
    s: TradingStore,
    entries: list[dict],
    open_by_portfolio: dict[str, list],
) -> dict[tuple[str, str], Decimal]:
    pairs: set[tuple[str, str]] = set()
    tf_by_portfolio = {e["portfolio"].id: e["instance"].timeframe for e in entries}
    for pid, positions in open_by_portfolio.items():
        tf = tf_by_portfolio.get(pid, "5m")
        for pos in positions:
            pairs.add((pos.instrument_id, tf))

    marks: dict[tuple[str, str], Decimal] = {}
    by_tf: dict[str, list[str]] = {}
    for instrument_id, tf in pairs:
        by_tf.setdefault(tf, []).append(instrument_id)

    for tf, instrument_ids in by_tf.items():
        unique_ids = list(dict.fromkeys(instrument_ids))
        closes = batch_latest_candle_closes(s, unique_ids, tf)
        for instrument_id, close in closes.items():
            marks[(instrument_id, tf)] = close
    return marks


def _run_batch_snapshots(s: TradingStore, entries: list[dict], started_at: datetime) -> int:
    """READ positions, COMPUTE equity, WRITE portfolio_snapshots + portfolios only."""
    portfolio_ids = [e["portfolio"].id for e in entries]
    if not portfolio_ids:
        return 0

    tf_by_portfolio = {e["portfolio"].id: e["instance"].timeframe for e in entries}
    open_by_portfolio = batch_open_positions_by_portfolio(s, portfolio_ids)
    marks_by_pair = _batch_marks(s, entries, open_by_portfolio)

    snapshots = []
    portfolios_to_update = []

    for entry in entries:
        portfolio = entry["portfolio"]
        positions = open_by_portfolio.get(portfolio.id, [])
        state = PortfolioState(portfolio=portfolio, positions=positions)
        tf = tf_by_portfolio.get(portfolio.id, "5m")
        marks = {
            pos.instrument_id: marks_by_pair[(pos.instrument_id, tf)]
            for pos in state.open_positions()
            if (pos.instrument_id, tf) in marks_by_pair
        }
        if marks:
            state.recalculate_equity(marks)
        else:
            state.portfolio.unrealized_pnl = Decimal("0")
            state.portfolio.equity = state.portfolio.balance
            state.portfolio.exposure_notional = Decimal("0")
            state.portfolio.reserved_capital = Decimal("0")

        snapshots.append(state.create_snapshot(started_at))
        portfolios_to_update.append(state.portfolio)

    s.update_portfolios_batch(portfolios_to_update)
    s.save_snapshots_batch(snapshots)
    s.flush()
    return len(portfolio_ids)


def snapshot_job(store: TradingStore | None = None) -> None:
    started_at = datetime.now(timezone.utc)

    def _run(s: TradingStore) -> None:
        _, _, entries = s.list_all_competition_entries()
        count = _run_batch_snapshots(s, entries, started_at)
        if count == 0:
            logger.warning("No active competition portfolios — snapshot job skipped")
            return

        s.session.commit()
        s.update_worker_status(
            "snapshot",
            {"status": "healthy", "last_run": started_at.isoformat(), "portfolios_snapshotted": count},
        )
        s.save_worker_run(
            run_id=str(uuid.uuid4()),
            worker_name="snapshot",
            started_at=started_at,
            jobs_processed=count,
        )
        logger.info("snapshot job completed for %d portfolio(s)", count)

    try:
        last_exc: Exception | None = None
        for attempt in range(MAX_DEADLOCK_RETRIES):
            try:
                if store is not None:
                    _run(store)
                else:
                    with session_scope() as session:
                        _run(TradingStore(session))
                return
            except OperationalError as exc:
                last_exc = exc
                if "deadlock" not in str(exc).lower() or attempt >= MAX_DEADLOCK_RETRIES - 1:
                    raise
                time.sleep(DEADLOCK_RETRY_BASE_SECONDS * (2**attempt))
        if last_exc is not None:
            raise last_exc
    except Exception as exc:
        logger.exception("snapshot_job failed")
        try:
            with session_scope() as session:
                s = TradingStore(session)
                s.update_worker_status(
                    "snapshot",
                    {"status": "error", "last_run": started_at.isoformat(), "error": str(exc)},
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
