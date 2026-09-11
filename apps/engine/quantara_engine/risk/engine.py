"""Risk engine evaluation."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import TYPE_CHECKING, Optional

from quantara_engine.competition.leverage import is_paper_competition_portfolio
from quantara_engine.domain.types import (
    Candle,
    Direction,
    ExecutionAssumptions,
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
from quantara_engine.execution.cost_profile import execution_assumptions_for
from quantara_engine.execution.economics import compute_executable_economics, tp_economically_valid
from quantara_engine.portfolio.currency import FxRateTable
from quantara_engine.risk.open_risk_guard import DEFAULT_OPEN_RISK_LIMITS, evaluate_all_open_risk_guards
from quantara_engine.risk.opportunity import opportunity_key_from_signal
from quantara_engine.risk.sizing import DEFAULT_RISK_ROUNDING_TOLERANCE_PCT, compute_position_size

if TYPE_CHECKING:
    from quantara_engine.persistence.store import TradingStore


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
    fx_rates: FxRateTable | None = None
    execution_assumptions: ExecutionAssumptions | None = None
    store: TradingStore | None = None


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
        paper_competition = is_paper_competition_portfolio(inp.portfolio.id)
        assumptions = inp.execution_assumptions or execution_assumptions_for(
            inp.instrument, inp.current_candle.close
        )

        opportunity_key = opportunity_key_from_signal(
            signal,
            symbol=inp.instrument.symbol,
            timeframe=inp.strategy_instance.timeframe,
            strategy_slug=inp.strategy_instance.strategy_slug,
            setup_candle_timestamp=inp.current_candle.timestamp,
        )

        if opportunity_key and inp.store:
            if inp.store.opportunity_consumed(
                inp.strategy_instance.id, opportunity_key, include_open=True
            ):
                return RiskDecision(
                    approved=False,
                    denial_reason="OPPORTUNITY_ALREADY_USED",
                    checks_failed=["opportunity_consumed"],
                )

        if not paper_competition:
            same_asset = [p for p in inp.open_positions if p.instrument_id == inp.instrument.id]
            if same_asset:
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

        mark = inp.current_candle.close
        virtual_leverage = paper_competition
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

        if not paper_competition and inp.portfolio.peak_equity > 0:
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

        qty, target_risk, expected_risk, deny = compute_position_size(
            portfolio=inp.portfolio,
            risk_profile=inp.risk_profile,
            instrument=inp.instrument,
            entry_reference=entry_ref,
            stop_loss=sl,
            direction=direction.value,
            open_positions=inp.open_positions,
            mark_price=mark,
            allow_virtual_leverage=virtual_leverage,
            fx_rates=inp.fx_rates,
            execution_assumptions=assumptions,
        )

        if deny:
            return RiskDecision(
                approved=False,
                denial_reason=f"RISK_DENIED: {deny}",
                checks_failed=["sizing"],
            )

        if signal.suggested_tp is not None and inp.fx_rates is not None:
            valid, econ = tp_economically_valid(
                direction,
                entry_ref,
                signal.suggested_tp,
                qty,
                inp.instrument,
                assumptions,
                inp.fx_rates,
            )
            if not valid:
                return RiskDecision(
                    approved=False,
                    denial_reason="TP_INSIDE_EXECUTION_COST",
                    checks_failed=["tp_economics"],
                    metadata={
                        "expected_net_reward_usd": float(econ.net_reward_usd),
                        "execution_cost_quote": float(econ.execution_cost_quote),
                        "strategy_tp": float(signal.suggested_tp),
                    },
                )

        if inp.fx_rates is not None:
            ok, guard_reason = evaluate_all_open_risk_guards(
                store=inp.store,
                portfolio=inp.portfolio,
                open_positions=inp.open_positions,
                instrument=inp.instrument,
                strategy_instance=inp.strategy_instance,
                incremental_risk_usd=expected_risk,
            )
            if not ok:
                return RiskDecision(
                    approved=False,
                    denial_reason=guard_reason or "OPEN_RISK_LIMIT",
                    checks_failed=["open_risk_guard"],
                )

        effective_risk_pct = (
            (expected_risk / inp.portfolio.equity * Decimal("100")).quantize(Decimal("0.0001"))
            if inp.portfolio.equity > 0
            else Decimal("0")
        )
        risk_audit = {
            "requested_tier_pct": str(inp.risk_profile.risk_per_trade_pct),
            "target_risk_usd": str(target_risk),
            "selected_quantity": str(qty),
            "effective_expected_risk_usd": str(expected_risk),
            "effective_expected_risk_pct": str(effective_risk_pct),
            "risk_rounding_tolerance_pct": str(DEFAULT_RISK_ROUNDING_TOLERANCE_PCT),
            "sl_execution_assumption": {
                "spread": str(assumptions.spread),
                "slippage_pct": str(assumptions.slippage_pct),
                "slippage_per_side": str(assumptions.slippage_per_side)
                if assumptions.slippage_per_side is not None
                else None,
            },
            "fx_jpy_per_usd": (
                str(inp.fx_rates.quote_per_usd.get("JPY"))
                if inp.fx_rates and inp.fx_rates.quote_per_usd.get("JPY")
                else None
            ),
            "risk_calculated_at": datetime.now(timezone.utc).isoformat(),
        }

        economics_meta = {"risk_audit": risk_audit}
        if signal.suggested_tp is not None and inp.fx_rates is not None:
            econ = compute_executable_economics(
                direction,
                entry_ref,
                sl,
                signal.suggested_tp,
                qty,
                inp.instrument,
                assumptions,
                inp.fx_rates,
            )
            economics_meta = {
                "opportunity_key": opportunity_key,
                "risk_tier": inp.risk_profile.slug if hasattr(inp.risk_profile, "slug") else None,
                "expected_entry_fill": float(econ.entry_fill),
                "expected_sl_fill": float(econ.sl_fill),
                "expected_tp_fill": float(econ.tp_fill) if econ.tp_fill else None,
                "gross_reward_usd": float(econ.gross_reward_usd),
                "net_expected_reward_usd": float(econ.net_reward_usd),
                "expected_loss_usd": float(econ.expected_loss_usd),
                "net_reward_risk_ratio": float(econ.net_reward_risk_ratio)
                if econ.net_reward_risk_ratio
                else None,
                "execution_cost_quote": float(econ.execution_cost_quote),
            }

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
            actual_risk_amount=expected_risk,
            signal_candle_timestamp=inp.current_candle.timestamp,
            risk_profile_id=inp.risk_profile.id,
            status=IntentStatus.PENDING_EXECUTION,
        )
        checks_passed.extend(["sizing", "sl_valid", "exposure_ok", "tp_economics", "open_risk_ok"])
        return RiskDecision(
            approved=True,
            intent=intent,
            checks_passed=checks_passed,
            metadata=economics_meta,
        )

    def _find_position(
        self, positions: list[Position], strategy_instance_id: str
    ) -> Position | None:
        for p in positions:
            if p.strategy_instance_id == strategy_instance_id:
                return p
        return None
