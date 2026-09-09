"""Opening Range Breakout Strategy v1.0.0 — US equities RTH only."""

from __future__ import annotations

from decimal import Decimal

from quantara_engine.domain.types import Signal, SignalAction, StrategyContext
from quantara_engine.strategies.base import BaseStrategy
from quantara_engine.strategies.opening_range_breakout.indicators import atr, candles_to_df
from quantara_engine.strategies.opening_range_breakout.session import (
    ORB_RANGE_COMPLETE,
    compute_opening_range,
    is_entry_cutoff_passed,
    is_market_closed,
    is_opening_range_complete,
    is_session_close_window,
    rth_session_date,
    to_et,
)


class OpeningRangeBreakoutV1(BaseStrategy):
    @classmethod
    def strategy_id(cls) -> str:
        return "opening-range-breakout"

    @classmethod
    def version(cls) -> str:
        return "1.0.0"

    @classmethod
    def name(cls) -> str:
        return "Opening Range Breakout"

    @classmethod
    def description(cls) -> str:
        return (
            "US equity opening range breakout on 5m candles — 30-minute range, "
            "ATR stop, 2R target, RTH only."
        )

    @classmethod
    def supported_instruments(cls) -> list[str]:
        return ["SPY", "QQQ", "NVDA", "AAPL", "MSFT"]

    @classmethod
    def supported_timeframes(cls) -> list[str]:
        return ["5m"]

    @classmethod
    def default_parameters(cls) -> dict:
        return {
            "opening_range_minutes": 30,
            "timeframe": "5m",
            "atr_period": 14,
            "atr_sl_multiplier": 1.5,
            "reward_risk_ratio": 2.0,
            "stop_mode": "atr",
            "entry_cutoff_et": "15:30",
            "max_trades_per_day": 1,
            "min_breakout_distance": 0.0,
            "volume_confirmation": False,
            "retest_required": False,
            "min_candles_required": 14,
        }

    @classmethod
    def parameters_schema(cls) -> dict:
        return {
            "type": "object",
            "properties": {
                "opening_range_minutes": {"type": "integer", "minimum": 15, "maximum": 60},
                "atr_period": {"type": "integer", "minimum": 7, "maximum": 21},
                "atr_sl_multiplier": {"type": "number", "minimum": 0.5, "maximum": 4.0},
                "reward_risk_ratio": {"type": "number", "minimum": 1.0, "maximum": 5.0},
                "stop_mode": {"type": "string", "enum": ["atr", "opening_range_opposite"]},
                "max_trades_per_day": {"type": "integer", "minimum": 1, "maximum": 5},
                "min_breakout_distance": {"type": "number", "minimum": 0.0},
                "volume_confirmation": {"type": "boolean"},
                "retest_required": {"type": "boolean"},
                "min_candles_required": {"type": "integer", "minimum": 14, "maximum": 50},
            },
        }

    @classmethod
    def risk_profile_compatibility(cls) -> list[str]:
        return [
            "very_conservative",
            "conservative",
            "balanced",
            "aggressive",
            "very_aggressive",
        ]

    def _params(self, context: StrategyContext) -> dict:
        return {**self.default_parameters(), **context.parameters}

    def _runtime(self, context: StrategyContext) -> dict:
        return dict(context.runtime or {})

    def evaluate(self, candles: list, context: StrategyContext) -> Signal:
        params = self._params(context)
        runtime = self._runtime(context)
        min_required = int(params["min_candles_required"])

        if len(candles) < min_required:
            return Signal(
                action=SignalAction.HOLD,
                reason=f"INSUFFICIENT_DATA: need {min_required} candles, have {len(candles)}",
            )

        current = candles[-1]
        ts = current.timestamp
        if is_market_closed(ts):
            return Signal(action=SignalAction.HOLD, reason="market_closed")

        session_date = rth_session_date(ts)
        if session_date is None:
            return Signal(action=SignalAction.HOLD, reason="market_closed")

        if runtime.get("has_open_position") and is_session_close_window(ts):
            return Signal(
                action=SignalAction.CLOSE,
                reason="session_close — flatten before RTH end",
                metadata={"session_date": session_date.isoformat()},
            )

        if not is_opening_range_complete(ts):
            local = to_et(ts)
            if local.time() < ORB_RANGE_COMPLETE:
                return Signal(
                    action=SignalAction.HOLD,
                    reason="opening_range_building",
                    metadata={"session_date": session_date.isoformat()},
                )
            return Signal(
                action=SignalAction.HOLD,
                reason="opening_range_incomplete",
                metadata={"session_date": session_date.isoformat()},
            )

        opening_range = compute_opening_range(candles, session_date)
        if opening_range is None:
            return Signal(
                action=SignalAction.HOLD,
                reason="opening_range_incomplete",
                metadata={"session_date": session_date.isoformat()},
            )

        metadata = {
            "session_date": session_date.isoformat(),
            "opening_range_high": float(opening_range.high),
            "opening_range_low": float(opening_range.low),
            "opening_range_size": float(opening_range.size),
            "effective_parameters": params,
        }

        trades_today = int(runtime.get("trades_today") or 0)
        max_trades = int(params["max_trades_per_day"])
        if trades_today >= max_trades:
            return Signal(
                action=SignalAction.HOLD,
                reason="trade_already_taken_today",
                metadata=metadata,
            )

        if is_entry_cutoff_passed(ts):
            return Signal(
                action=SignalAction.HOLD,
                reason="entry_cutoff_passed",
                metadata=metadata,
            )

        close = Decimal(str(current.close))
        high = Decimal(str(current.high))
        min_distance = Decimal(str(params["min_breakout_distance"]))

        df = candles_to_df(candles)
        atr_vals = atr(df, int(params["atr_period"]))
        atr_val = float(atr_vals.iloc[-1])
        if atr_val != atr_val:  # NaN
            return Signal(
                action=SignalAction.HOLD,
                reason="waiting_for_breakout",
                metadata={**metadata, "atr": None},
            )
        metadata["atr"] = atr_val

        sl_mult = float(params["atr_sl_multiplier"])
        rr = float(params["reward_risk_ratio"])
        stop_distance = Decimal(str(atr_val * sl_mult))

        low = Decimal(str(current.low))

        # LONG — close above range (wick-only rejected)
        if high > opening_range.high + min_distance and close <= opening_range.high:
            return Signal(
                action=SignalAction.HOLD,
                reason="waiting_for_breakout",
                metadata=metadata,
            )
        if close > opening_range.high + min_distance:
            sl = close - stop_distance
            risk_per_unit = close - sl
            tp = close + risk_per_unit * Decimal(str(rr))
            return Signal(
                action=SignalAction.BUY,
                reason="breakout_long_confirmed",
                confidence=Decimal("0.65"),
                suggested_sl=sl,
                suggested_tp=tp,
                metadata=metadata,
            )

        # SHORT — close below range (wick-only rejected)
        if low < opening_range.low - min_distance and close >= opening_range.low:
            return Signal(
                action=SignalAction.HOLD,
                reason="waiting_for_breakout",
                metadata=metadata,
            )
        if close < opening_range.low - min_distance:
            sl = close + stop_distance
            risk_per_unit = sl - close
            tp = close - risk_per_unit * Decimal(str(rr))
            return Signal(
                action=SignalAction.SELL,
                reason="breakout_short_confirmed",
                confidence=Decimal("0.65"),
                suggested_sl=sl,
                suggested_tp=tp,
                metadata=metadata,
            )

        return Signal(
            action=SignalAction.HOLD,
            reason="waiting_for_breakout",
            metadata=metadata,
        )
