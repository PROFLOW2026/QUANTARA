"""Lightweight live execution of pending Paper intents — independent of strategy evaluation."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from quantara_engine.competition.constants import ACTIVE_COMPETITION_EXPERIMENT_ID
from quantara_engine.competition.orb_constants import ORB_COMPETITION_EXPERIMENT_ID
from quantara_engine.core.clock import BacktestClock
from quantara_engine.domain.types import IntentStatus, Mode
from quantara_engine.execution.paper_broker import PaperBrokerAdapter
from quantara_engine.market_data.polling import is_bar_complete
from quantara_engine.pipeline.candle_processor import CandleProcessor
from quantara_engine.persistence.store import TradingStore

logger = logging.getLogger(__name__)

CANDLE_LOOKBACK = 120


@dataclass(frozen=True)
class PendingIntentWork:
    entry: dict
    intent_id: str
    exec_ts: datetime
    sort_key: tuple


def _expire_stale_intents(store: TradingStore, now: datetime) -> int:
    from quantara_engine.competition.multi_strategy_constants import (
        MEAN_REVERSION_EXPERIMENT_ID,
        MOMENTUM_CONTINUATION_EXPERIMENT_ID,
        VOLATILITY_SQUEEZE_EXPERIMENT_ID,
    )

    expired = store.cancel_stale_pending_intents(ACTIVE_COMPETITION_EXPERIMENT_ID, now)
    expired += store.cancel_stale_pending_intents(ORB_COMPETITION_EXPERIMENT_ID, now)
    for experiment_id in (
        MEAN_REVERSION_EXPERIMENT_ID,
        VOLATILITY_SQUEEZE_EXPERIMENT_ID,
        MOMENTUM_CONTINUATION_EXPERIMENT_ID,
    ):
        expired += store.cancel_stale_pending_intents(experiment_id, now)
    return expired


def _collect_pending_work(store: TradingStore, now: datetime) -> list[PendingIntentWork]:
    _, _, entries = store.list_all_competition_entries()

    work: list[PendingIntentWork] = []
    for entry in entries:
        portfolio = entry["portfolio"]
        instance = entry["instance"]
        instrument = store.get_instrument_by_id(instance.instrument_id)
        if not instrument:
            continue
        pending = store.list_pending_order_intents(portfolio.id, instance.id)
        for intent in pending:
            if intent.execution_candle_timestamp is None:
                continue
            if intent.status != IntentStatus.PENDING_EXECUTION:
                continue
            idem = f"intent:{intent.id}"
            work.append(
                PendingIntentWork(
                    entry=entry,
                    intent_id=intent.id,
                    exec_ts=intent.execution_candle_timestamp,
                    sort_key=(intent.execution_candle_timestamp, idem, portfolio.id),
                )
            )
    work.sort(key=lambda row: row.sort_key)
    return work


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
    portfolios_checked: set[str] = set()
    errors: list[str] = []

    work_items = _collect_pending_work(store, now)
    if not work_items:
        return {
            "expired_intents": expired,
            "portfolios_checked": 0,
            "fills_attempted": 0,
            "errors": errors,
        }

    processors: dict[tuple[str, str], CandleProcessor] = {}

    for item in work_items:
        entry = item.entry
        portfolio = entry["portfolio"]
        instance = entry["instance"]
        instrument = store.get_instrument_by_id(instance.instrument_id)
        if not instrument:
            continue

        proc_key = (portfolio.id, instance.id)
        if proc_key not in processors:
            candles = store.list_recent_candles(
                instrument.id, instance.timeframe, limit=CANDLE_LOOKBACK
            )
            if not candles:
                continue
            latest_completed = None
            for candle in candles:
                if is_bar_complete(candle.timestamp, instance.timeframe, now):
                    latest_completed = candle.timestamp

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
                enforce_catchup_stale_guard=False,
            )
            processor.all_candles = candles
            pending = store.list_pending_order_intents(portfolio.id, instance.id)
            processor.pending_intents = list(pending)
            processor._persisted_intents = {intent.id for intent in pending}
            processors[proc_key] = processor

        processor = processors[proc_key]
        intent = next((i for i in processor.pending_intents if i.id == item.intent_id), None)
        if intent is None or intent.status != IntentStatus.PENDING_EXECUTION:
            continue

        candle = next(
            (c for c in processor.all_candles if c.timestamp == item.exec_ts),
            None,
        )
        if candle is None:
            continue
        if not is_bar_complete(candle.timestamp, instance.timeframe, now):
            continue
        candle_index = next(
            (idx for idx, row in enumerate(processor.all_candles) if row.timestamp == item.exec_ts),
            None,
        )
        if candle_index is None:
            continue

        portfolios_checked.add(portfolio.id)
        try:
            pending_before = sum(
                1
                for row in processor.pending_intents
                if row.status == IntentStatus.PENDING_EXECUTION
            )
            processor.pending_intents = [intent]
            processor.execute_pending_on_candle(candle_index)
            processor.pending_intents = store.list_pending_order_intents(portfolio.id, instance.id)
            pending_after = sum(
                1
                for row in processor.pending_intents
                if row.status == IntentStatus.PENDING_EXECUTION
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
        "portfolios_checked": len(portfolios_checked),
        "fills_attempted": fills,
        "errors": errors,
    }
