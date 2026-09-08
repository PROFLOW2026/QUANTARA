"""Canonical candle processing pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

from quantara_engine.core.clock import BacktestClock
from quantara_engine.domain.types import (
    Candle,
    DecisionLogEntry,
    DecisionType,
    ExitReason,
    Instrument,
    IntentStatus,
    Mode,
    OrderIntent,
    Portfolio,
    PortfolioStatus,
    Position,
    RiskProfile,
    SignalAction,
    StrategyInstance,
    new_id,
)
from quantara_engine.execution.paper_broker import PaperBrokerAdapter
from quantara_engine.market_data.polling import timeframe_minutes
from quantara_engine.portfolio.service import PortfolioState
from quantara_engine.risk.engine import RiskEngine, RiskEvaluationInput
from quantara_engine.strategies.base import BaseStrategy
from quantara_engine.strategies.registry import get

if TYPE_CHECKING:
    from quantara_engine.persistence.store import TradingStore


@dataclass
class PipelineResult:
    decisions: list[DecisionLogEntry] = field(default_factory=list)
    pending_intents: list[OrderIntent] = field(default_factory=list)
    processed_key: str = ""


class CandleProcessor:
    """Implements canonical flow: execute pending → SL/TP → mark → strategy → queue."""

    def __init__(
        self,
        portfolio_state: PortfolioState,
        strategy_instance: StrategyInstance,
        instrument: Instrument,
        risk_profile: RiskProfile,
        broker: PaperBrokerAdapter,
        clock: BacktestClock | None = None,
        store: TradingStore | None = None,
        mode: Mode = Mode.PAPER,
        backtest_run_id: str | None = None,
    ) -> None:
        self.state = portfolio_state
        self.instance = strategy_instance
        self.instrument = instrument
        self.risk_profile = risk_profile
        self.broker = broker
        self.risk_engine = RiskEngine()
        self.clock = clock or BacktestClock()
        self.store = store
        self.mode = mode
        self.backtest_run_id = backtest_run_id
        if store is not None:
            store.mode = mode
            store.backtest_run_id = backtest_run_id
        self.pending_intents: list[OrderIntent] = []
        self.processed_keys: set[str] = set()
        self.all_candles: list[Candle] = []
        self.decisions: list[DecisionLogEntry] = []
        self._persisted_intents: set[str] = set()

    def _strategy(self) -> BaseStrategy:
        cls = get(self.instance.strategy_slug, self.instance.strategy_version)
        return cls()

    def _flush_store(self) -> None:
        if self.store:
            self.store.flush()

    def _log(
        self,
        candle: Candle,
        decision_type: DecisionType,
        message: str,
        signal_id: str | None = None,
        metadata: dict | None = None,
    ) -> DecisionLogEntry:
        entry = DecisionLogEntry(
            id=new_id(),
            strategy_instance_id=self.instance.id,
            instrument_id=candle.instrument_id,
            candle_timestamp=candle.timestamp,
            decision_type=decision_type,
            message=message,
            signal_id=signal_id,
            metadata=metadata or {},
        )
        self.decisions.append(entry)
        if self.store:
            self.store.save_decision(entry)
            self._flush_store()
        return entry

    def _persist_signal(self, signal, signal_id: str, candle: Candle) -> None:
        if not self.store:
            return
        self.store.save_signal(
            signal_id=signal_id,
            signal=signal,
            strategy_instance_id=self.instance.id,
            strategy_version_id=self.instance.strategy_version_id,
            instrument_id=candle.instrument_id,
            candle_timestamp=candle.timestamp,
        )
        self._flush_store()

    def _persist_intent(self, intent: OrderIntent) -> None:
        if not self.store or intent.id in self._persisted_intents:
            return
        self.store.save_order_intent(intent)
        self._persisted_intents.add(intent.id)
        self._flush_store()

    def _persist_execution(
        self,
        intent: OrderIntent | None,
        order,
        fill,
        side: str,
        candle: Candle,
        position: Position | None = None,
        trade=None,
    ) -> None:
        if not self.store:
            return
        if intent:
            self._persist_intent(intent)
            self.store.update_order_intent_status(intent.id, intent.status)
        self.store.save_order(
            order,
            strategy_instance_id=self.instance.id,
            signal_id=intent.signal_id if intent else None,
        )
        if position and not trade:
            self.store.save_position(position)
        fill_id = new_id()
        qty = intent.quantity if intent else (position.quantity if position else order.quantity)
        self.store.save_fill(
            fill_id=fill_id,
            order_id=order.id,
            fill=fill,
            side=side,
            filled_at=candle.timestamp,
            quantity=qty,
            position_id=position.id if position else None,
        )
        if position and trade:
                self.store.update_position_closed(
                    position.id, candle.timestamp, fill.fill_price
                )
                self.store.save_trade(trade)
        self.store.update_portfolio(self.state.portfolio)
        self._flush_store()

    def _persist_snapshot(self, snap) -> None:
        if not self.store:
            return
        self.store.update_portfolio(self.state.portfolio)
        self.store.save_snapshot(snap)
        self._flush_store()

    def evaluate_signal(self, candle_index: int):
        """Evaluate strategy once on candles up to index (no persistence)."""
        candle = self.all_candles[candle_index]
        visible = self.all_candles[: candle_index + 1]
        strategy = self._strategy()
        params = {**strategy.default_parameters(), **self.instance.parameter_overrides}
        from quantara_engine.domain.types import StrategyContext

        ctx = StrategyContext(
            instrument_id=candle.instrument_id,
            timeframe=candle.timeframe,
            parameters=params,
        )
        return strategy.evaluate(visible, ctx), candle

    def process_candle(
        self,
        candle_index: int,
        *,
        shared_signal=None,
    ) -> PipelineResult:
        candle = self.all_candles[candle_index]
        key = f"{candle.instrument_id}:{candle.timeframe}:{candle.timestamp.isoformat()}"
        if key in self.processed_keys:
            return PipelineResult(decisions=[], pending_intents=[], processed_key=key)
        self.processed_keys.add(key)

        if isinstance(self.clock, BacktestClock):
            self.clock.set_candle_time(candle.timestamp)

        # 1. Execute pending intents at this candle open
        self._execute_pending(candle)

        # 2. Check SL/TP on candle OHLC
        self._check_sl_tp(candle)

        # 3. Update unrealized P&L at close
        self.state.recalculate_equity(candle.close)
        if self.store:
            self.store.update_portfolio(self.state.portfolio)
            for pos in self.state.open_positions():
                self.store.update_open_position_mark(
                    pos.id, pos.current_price, pos.unrealized_pnl
                )
            self._flush_store()

        from quantara_engine.domain.types import Signal

        if shared_signal is not None:
            signal: Signal = shared_signal
        else:
            signal, _ = self.evaluate_signal(candle_index)

        signal_id = new_id()
        self._persist_signal(signal, signal_id, candle)

        visible = self.all_candles[: candle_index + 1]
        self._handle_signal_decisions(signal, candle, signal_id, visible)

        # 6. Snapshot
        snap = self.state.create_snapshot(candle.timestamp)
        self._persist_snapshot(snap)

        return PipelineResult(
            decisions=self.decisions[-5:],
            pending_intents=list(self.pending_intents),
            processed_key=key,
        )

    def _handle_signal_decisions(
        self,
        signal,
        candle: Candle,
        signal_id: str,
        visible: list,
    ) -> None:
        if signal.action == SignalAction.HOLD:
            if signal.reason.startswith("NO_SETUP") or signal.reason.startswith("INSUFFICIENT"):
                dtype = (
                    DecisionType.NO_SETUP
                    if signal.reason.startswith("NO_SETUP")
                    else DecisionType.HOLD
                )
            else:
                dtype = DecisionType.HOLD
            self._log(candle, dtype, signal.reason, signal_id)
        elif signal.action == SignalAction.BUY:
            self._log(candle, DecisionType.BUY_SIGNAL, signal.reason, signal_id)
            self._handle_trade_signal(signal, candle, signal_id, visible)
        elif signal.action == SignalAction.SELL:
            self._log(candle, DecisionType.SELL_SIGNAL, signal.reason, signal_id)
            self._handle_trade_signal(signal, candle, signal_id, visible)
        elif signal.action == SignalAction.CLOSE:
            self._log(candle, DecisionType.CLOSE_SIGNAL, signal.reason, signal_id)
            self._handle_close_signal(signal, candle, signal_id)

    def _execute_pending(self, candle: Candle) -> None:
        to_execute = [
            i
            for i in self.pending_intents
            if i.status == IntentStatus.PENDING_EXECUTION
            and i.execution_candle_timestamp == candle.timestamp
        ]
        for intent in to_execute:
            if intent.is_close and intent.position_id:
                position = next(
                    (p for p in self.state.open_positions() if p.id == intent.position_id),
                    None,
                )
                if position:
                    order, fill = self.broker.execute_entry(intent, candle)
                    trade = self.state.close_position(
                        position, fill, ExitReason.STRATEGY, candle.timestamp
                    )
                    intent.status = IntentStatus.EXECUTED
                    self._persist_execution(intent, order, fill, "exit", candle, position, trade)
            else:
                open_for_instance = [
                    p
                    for p in self.state.open_positions()
                    if p.strategy_instance_id == self.instance.id
                ]
                if open_for_instance and not intent.is_close:
                    self._log(
                        candle,
                        DecisionType.POSITION_OPEN,
                        "Skipped — position already exists",
                    )
                    intent.status = IntentStatus.REJECTED
                    if self.store:
                        self.store.update_order_intent_status(
                            intent.id, IntentStatus.REJECTED, "position_exists"
                        )
                        self._flush_store()
                    continue
                order, fill = self.broker.execute_entry(intent, candle)
                position = self.state.open_position_from_fill(
                    intent,
                    fill,
                    order,
                    self.instance.strategy_version_id,
                    self.instrument.id,
                    candle.timestamp,
                )
                intent.status = IntentStatus.EXECUTED
                self._persist_execution(intent, order, fill, "entry", candle, position)
        self.pending_intents = [
            i for i in self.pending_intents if i.status == IntentStatus.PENDING_EXECUTION
        ]

    def _check_sl_tp(self, candle: Candle) -> None:
        from quantara_engine.execution.exit_triggers import detect_exit_trigger

        for position in list(self.state.open_positions()):
            if position.instrument_id != candle.instrument_id:
                continue
            trigger = detect_exit_trigger(position, candle)
            if not trigger:
                continue
            reason, trigger_price = trigger
            order, fill = self.broker.execute_exit_at_trigger(
                position.direction,
                position.quantity,
                candle,
                trigger_price,
                self.state.portfolio.id,
            )
            trade = self.state.close_position(position, fill, reason, candle.timestamp)
            self._persist_execution(None, order, fill, "exit", candle, position, trade)
            if reason == ExitReason.SL:
                self._log(candle, DecisionType.SL_TRIGGERED, f"SL hit at {trigger_price}")
            else:
                self._log(candle, DecisionType.TP_TRIGGERED, f"TP hit at {trigger_price}")

    def _handle_trade_signal(self, signal, candle: Candle, signal_id: str, visible: list) -> None:
        if self.state.portfolio.status != PortfolioStatus.ACTIVE:
            self._log(candle, DecisionType.TRADING_HALTED, "Portfolio halted")
            return

        open_positions = self.state.open_positions()
        if any(p.strategy_instance_id == self.instance.id for p in open_positions):
            self._log(candle, DecisionType.POSITION_OPEN, "Skipped — position already exists")
            return

        atr_value = None
        if signal.metadata and "atr" in signal.metadata:
            atr_value = Decimal(str(signal.metadata["atr"]))

        decision = self.risk_engine.evaluate(
            RiskEvaluationInput(
                signal=signal,
                strategy_instance=self.instance,
                portfolio=self.state.portfolio,
                open_positions=open_positions,
                risk_profile=self.risk_profile,
                current_candle=candle,
                instrument=self.instrument,
                signal_id=signal_id,
                atr_value=atr_value,
            )
        )

        if not decision.approved or decision.intent is None:
            self._log(
                candle,
                DecisionType.RISK_DENIED,
                decision.denial_reason or "DENIED",
                signal_id,
            )
            if decision.should_halt:
                self.state.portfolio.status = PortfolioStatus.HALTED
                if self.store:
                    self.store.update_portfolio(self.state.portfolio)
                    self._flush_store()
            return

        if self.store:
            existing = self.store.find_pending_intent_for_signal_candle(
                self.instance.id, candle.timestamp
            )
            if existing:
                if all(i.id != existing.id for i in self.pending_intents):
                    self.pending_intents.append(existing)
                    self._persisted_intents.add(existing.id)
                return

        intent = decision.intent
        candle_index = self._candle_index(candle)
        if candle_index is None:
            return

        intent.execution_candle_timestamp = self._next_execution_timestamp(
            candle, candle_index
        )
        self.pending_intents.append(intent)
        self._persist_intent(intent)
        from quantara_engine.competition.leverage import compute_sizing_metrics, is_competition_portfolio

        metrics = compute_sizing_metrics(
            intent.quantity,
            candle.close,
            self.state.portfolio.equity,
            intent.target_risk_amount,
            intent.actual_risk_amount,
        )
        msg = (
            f"RISK_APPROVED: qty={intent.quantity}, target_risk=${intent.target_risk_amount}, "
            f"actual_risk=${intent.actual_risk_amount}, SL={intent.stop_loss}"
        )
        if is_competition_portfolio(self.state.portfolio.id):
            msg += (
                f", exposure={metrics['exposure_pct']}%, "
                f"virtual_leverage={metrics['virtual_leverage']}x"
            )
        self._log(
            candle,
            DecisionType.RISK_APPROVED,
            msg,
            signal_id,
            metadata={
                "target_risk_pct": str(metrics["target_risk_pct"]),
                "actual_risk_pct": str(metrics["actual_risk_pct"]),
                "exposure_pct": str(metrics["exposure_pct"]),
                "virtual_leverage": str(metrics["virtual_leverage"]),
                "notional": str(metrics["notional"]),
            },
        )

    def _handle_close_signal(self, signal, candle: Candle, signal_id: str) -> None:
        decision = self.risk_engine.evaluate(
            RiskEvaluationInput(
                signal=signal,
                strategy_instance=self.instance,
                portfolio=self.state.portfolio,
                open_positions=self.state.open_positions(),
                risk_profile=self.risk_profile,
                current_candle=candle,
                instrument=self.instrument,
                signal_id=signal_id,
            )
        )
        if decision.approved and decision.intent:
            idx = self._candle_index(candle)
            if idx is not None:
                decision.intent.execution_candle_timestamp = self._next_execution_timestamp(
                    candle, idx
                )
                self.pending_intents.append(decision.intent)
                self._persist_intent(decision.intent)

    def _next_execution_timestamp(self, candle: Candle, candle_index: int) -> datetime:
        if candle_index + 1 < len(self.all_candles):
            return self.all_candles[candle_index + 1].timestamp
        return candle.timestamp + timedelta(minutes=timeframe_minutes(candle.timeframe))

    def _candle_index(self, candle: Candle) -> int | None:
        for i, c in enumerate(self.all_candles):
            if c.timestamp == candle.timestamp:
                return i
        return None

    def run_all(self) -> PortfolioState:
        for i in range(len(self.all_candles)):
            self.process_candle(i)
        return self.state
