"""Lightweight live execution of pending Paper intents — independent of strategy evaluation."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from quantara_engine.competition.constants import ACTIVE_COMPETITION_EXPERIMENT_ID
from quantara_engine.competition.orb_constants import ORB_COMPETITION_EXPERIMENT_ID
from quantara_engine.core.clock import BacktestClock
from quantara_engine.domain.types import ExecutionAssumptions, IntentStatus, Mode
from quantara_engine.execution.paper_broker import PaperBrokerAdapter
from quantara_engine.market_data.polling import is_bar_complete
from quantara_engine.pipeline.candle_processor import CandleProcessor
from quantara_engine.persistence.store import TradingStore

logger = logging.getLogger(__name__)

CANDLE_LOOKBACK = 120


def _expire_stale_intents(store: TradingStore, now: datetime) -> int:
    expired = store.cancel_stale_pending_intents(ACTIVE_COMPETITION_EXPERIMENT_ID, now)
    expired += store.cancel_stale_pending_intents(ORB_COMPETITION_EXPERIMENT_ID, now)
    return expired


def execute_pending_intents_live(store: TradingStore, now: datetime | None = None) -> dict:
    """Fill eligible pending intents without running strategy logic."""
    from quantara_engine.trading.trading_controls import allows_new_entries, load_trading_control

    now = now or datetime.now(timezone.utc)
    if not allows_new_entries(load_trading_control(store.get_settings_dict())):
        expired = _expire_stale_intents(store, now)
        return {
            "status": "skipped",
            "reason": "new_entries_blocked",
            "expired_intents": expired,
            "fills_attempted": 0,
            "portfolios_checked": 0,
            "errors": [],
        }
    expired = _expire_stale_intents(store, now)
    fills = 0
    portfolios_checked = 0
    errors: list[str] = []

    entries = store.list_competition_entries()
    if store.is_orb_competition_enabled():
        entries = entries + store.list_orb_competition_entries()

    for entry in entries:
        portfolio = entry["portfolio"]
        instance = entry["instance"]
        instrument = store.get_instrument_by_id(instance.instrument_id)
        if not instrument:
            continue

        pending = store.list_pending_order_intents(portfolio.id, instance.id)
        if not pending:
            continue

        exec_timestamps = {
            intent.execution_candle_timestamp
            for intent in pending
            if intent.execution_candle_timestamp is not None
        }
        if not exec_timestamps:
            continue

        candles = store.list_recent_candles(
            instrument.id, instance.timeframe, limit=CANDLE_LOOKBACK
        )
        if not candles:
            continue

        candle_by_ts = {c.timestamp: c for c in candles}
        latest_completed = None
        for candle in candles:
            if is_bar_complete(candle.timestamp, instance.timeframe, now):
                latest_completed = candle.timestamp

        portfolios_checked += 1
        state = store.load_portfolio_state(portfolio.id)
        from quantara_engine.execution.cost_profile import execution_assumptions_for

        broker = PaperBrokerAdapter(instrument.id, execution_assumptions_for(instrument))
        processor = CandleProcessor(
            portfolio_state=state,
            strategy_instance=instance,
            instrument=instrument,
            risk_profile=entry["risk_profile"],
            broker=broker,
            clock=BacktestClock(),
            store=store,
            mode=Mode.PAPER,
            latest_completed_timestamp=latest_completed,
            allow_live_execution=True,
            execution_now=now,
            manage_exits=False,
        )
        processor.all_candles = candles
        processor.pending_intents = list(pending)
        processor._persisted_intents = {intent.id for intent in pending}

        for exec_ts in sorted(exec_timestamps):
            candle = candle_by_ts.get(exec_ts)
            if candle is None:
                continue
            candle_index = next(
                (idx for idx, row in enumerate(candles) if row.timestamp == exec_ts),
                None,
            )
            if candle_index is None:
                continue
            try:
                pending_before = sum(
                    1
                    for intent in processor.pending_intents
                    if intent.status == IntentStatus.PENDING_EXECUTION
                )
                processor.execute_pending_on_candle(candle_index)
                pending_after = sum(
                    1
                    for intent in processor.pending_intents
                    if intent.status == IntentStatus.PENDING_EXECUTION
                )
                if pending_after < pending_before:
                    fills += pending_before - pending_after
            except Exception as exc:
                msg = f"{instrument.symbol}/{instance.timeframe}: {exc}"
                logger.exception("execute_pending_intents failed for %s", msg)
                errors.append(msg)

        store.session.commit()

    return {
        "expired_intents": expired,
        "portfolios_checked": portfolios_checked,
        "fills_attempted": fills,
        "errors": errors,
    }
