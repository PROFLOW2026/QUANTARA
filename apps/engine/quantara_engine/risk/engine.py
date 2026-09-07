"""Risk engine evaluation."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional

from quantara_engine.domain.types import (
    Candle,
    Direction,
    Instrument,
    OrderIntent,
    Portfolio,
    PortfolioStatus,
    Position,
    RiskProfile,
    Signal,
    SignalAction,
    StrategyInstance,
    IntentStatus,
    new_id,
)
from quantara_engine.risk.sizing import compute_position_size
from quantara_engine.competition.leverage import is_competition_portfolio


@dataclass
class RiskEvaluationInput:
    signal: Signal
    strategy_instance: StrategyInstance
    portfolio: Portfolio
    open_positions: list[Position]
    risk_profile: RiskProfile
    current_candle: Candle
    instrument: Instrument
    signal_id: str = ""
    all_active_instances: list[StrategyInstance] = field(default_factory=list)
    atr_value: Optional[Decimal] = None


@dataclass
class RiskDecision:
    approved: bool
    intent: Optional[OrderIntent] = None
    denial_reason: Optional[str] = None
    checks_passed: list[str] = field(default_factory=list)
    checks_failed: list[str] = field(default_factory=list)
    metadata: Optional[dict] = None
    should_halt: bool = False


class RiskEngine:
    def evaluate(self, inp: RiskEvaluationInput) -> RiskDecision:
        signal = inp.signal
        checks_passed: list[str] = []
        checks_failed: list[str] = []

        if inp.portfolio.status != PortfolioStatus.ACTIVE:
            return RiskDecision(
                approved=False,
                denial_reason="TRADING_HALTED",
                checks_failed=["trading_halt"],
            )

        if signal.action in (SignalAction.HOLD, SignalAction.MODIFY):
            return RiskDecision(approved=False, denial_reason="SKIP_HOLD_MODIFY")

        if signal.action == SignalAction.CLOSE:
            position = self._find_position(inp.open_positions, inp.strategy_instance.id)
            if not position:
                return RiskDecision(
                    approved=False,
                    denial_reason="NO_POSITION_TO_CLOSE",
                    checks_failed=["no_position"],
                )
            close_dir = Direction.SHORT if position.direction == Direction.LONG else Direction.LONG
            intent = OrderIntent(
                id=new_id(),
                signal_id=inp.signal_id,
                strategy_instance_id=inp.strategy_instance.id,
                portfolio_id=inp.portfolio.id,
                direction=close_dir,
                quantity=position.quantity,
                stop_loss=position.stop_loss,
                take_profit=position.take_profit,
                target_risk_amount=Decimal("0"),
                actual_risk_amount=Decimal("0"),
                signal_candle_timestamp=inp.current_candle.timestamp,
                risk_profile_id=inp.risk_profile.id,
                status=IntentStatus.PENDING_EXECUTION,
                is_close=True,
                position_id=position.id,
            )
            checks_passed.append("close_signal")
            return RiskDecision(approved=True, intent=intent, checks_passed=checks_passed)

        if signal.action not in (SignalAction.BUY, SignalAction.SELL):
            return RiskDecision(approved=False, denial_reason="INVALID_ACTION")

        direction = Direction.LONG if signal.action == SignalAction.BUY else Direction.SHORT

        # Open position check
        same_dir = [
            p
            for p in inp.open_positions
            if p.instrument_id == inp.instrument.id and p.direction == direction
        ]
        if same_dir:
            return RiskDecision(
                approved=False,
                denial_reason="POSITION_ALREADY_OPEN",
                checks_failed=["position_exists"],
            )

        if len(inp.open_positions) >= inp.risk_profile.max_open_positions:
            return RiskDecision(
                approved=False,
                denial_reason="MAX_OPEN_POSITIONS",
                checks_failed=["max_positions"],
            )

        # Exposure check — legacy paper only; competition uses virtual leverage sizing
        mark = inp.current_candle.close
        virtual_leverage = is_competition_portfolio(inp.portfolio.id)
        if not virtual_leverage:
            total_exposure = sum(p.quantity * mark for p in inp.open_positions)
            exposure_pct = (
                (total_exposure / inp.portfolio.equity * Decimal("100"))
                if inp.portfolio.equity > 0
                else Decimal("0")
            )
            if exposure_pct > inp.risk_profile.max_total_exposure_pct:
                return RiskDecision(
                    approved=False,
                    denial_reason=f"MAX_EXPOSURE ({exposure_pct}% > {inp.risk_profile.max_total_exposure_pct}%)",
                    checks_failed=["max_exposure"],
                )

        # Drawdown check
        if inp.portfolio.peak_equity > 0:
            dd_pct = (
                (inp.portfolio.peak_equity - inp.portfolio.equity)
                / inp.portfolio.peak_equity
                * Decimal("100")
            )
            if dd_pct >= inp.risk_profile.max_drawdown_pct:
                return RiskDecision(
                    approved=False,
                    denial_reason="MAX_DRAWDOWN + HALT TRIGGERED",
                    checks_failed=["max_drawdown"],
                    should_halt=True,
                )

        # SL validation
        if signal.suggested_sl is None:
            return RiskDecision(
                approved=False,
                denial_reason="INVALID_STOP_LOSS (missing)",
                checks_failed=["sl_missing"],
            )

        entry_ref = inp.current_candle.close
        sl = signal.suggested_sl
        sl_distance = abs(entry_ref - sl)

        if signal.action == SignalAction.BUY and sl >= entry_ref:
            return RiskDecision(
                approved=False,
                denial_reason="INVALID_STOP_LOSS (long SL must be below entry)",
                checks_failed=["sl_direction"],
            )
        if signal.action == SignalAction.SELL and sl <= entry_ref:
            return RiskDecision(
                approved=False,
                denial_reason="INVALID_STOP_LOSS (short SL must be above entry)",
                checks_failed=["sl_direction"],
            )

        if inp.atr_value and inp.atr_value > 0:
            min_dist = Decimal("0.5") * inp.atr_value
            max_dist = Decimal("5.0") * inp.atr_value
            if sl_distance < min_dist:
                return RiskDecision(
                    approved=False,
                    denial_reason="INVALID_STOP_LOSS (too tight)",
                    checks_failed=["sl_too_tight"],
                )
            if sl_distance > max_dist:
                return RiskDecision(
                    approved=False,
                    denial_reason="INVALID_STOP_LOSS (too wide)",
                    checks_failed=["sl_too_wide"],
                )

        qty, target_risk, actual_risk, deny = compute_position_size(
            portfolio=inp.portfolio,
            risk_profile=inp.risk_profile,
            instrument=inp.instrument,
            entry_reference=entry_ref,
            stop_loss=sl,
            direction=direction.value,
            open_positions=inp.open_positions,
            mark_price=mark,
            allow_virtual_leverage=virtual_leverage,
        )

        if deny:
            return RiskDecision(
                approved=False,
                denial_reason=f"RISK_DENIED: {deny}",
                checks_failed=["sizing"],
            )

        intent = OrderIntent(
            id=new_id(),
            signal_id=inp.signal_id,
            strategy_instance_id=inp.strategy_instance.id,
            portfolio_id=inp.portfolio.id,
            direction=direction,
            quantity=qty,
            stop_loss=sl,
            take_profit=signal.suggested_tp,
            target_risk_amount=target_risk,
            actual_risk_amount=actual_risk,
            signal_candle_timestamp=inp.current_candle.timestamp,
            risk_profile_id=inp.risk_profile.id,
            status=IntentStatus.PENDING_EXECUTION,
        )
        checks_passed.extend(["sizing", "sl_valid", "exposure_ok"])
        return RiskDecision(approved=True, intent=intent, checks_passed=checks_passed)

    def _find_position(
        self, positions: list[Position], strategy_instance_id: str
    ) -> Position | None:
        for p in positions:
            if p.strategy_instance_id == strategy_instance_id:
                return p
        return None
