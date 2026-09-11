"""Opening Range Breakout Strategy v1.0.0 — multi-session ORB."""

from __future__ import annotations

from decimal import Decimal

from quantara_engine.domain.types import Signal, SignalAction, StrategyContext
from quantara_engine.market_data.active_universe import list_active_db_symbols
from quantara_engine.strategies.base import BaseStrategy
from quantara_engine.strategies.opening_range_breakout.indicators import atr, candles_to_df
from quantara_engine.strategies.opening_range_breakout.session import ORB_RANGE_COMPLETE, to_et
from quantara_engine.risk.opportunity import orb_opportunity_key
from quantara_engine.strategies.opening_range_breakout.session_router import get_orb_session_handler


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
            "Opening range breakout on 5m candles — US equities use RTH range; "
            "crypto/FX use UTC daily range. ATR stop, 2R target."
        )

    @classmethod
    def supported_instruments(cls) -> list[str]:
        return list(list_active_db_symbols())

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

    def _session(self, context: StrategyContext):
        db_symbol = self._runtime(context).get("db_symbol", "NVDA")
        return get_orb_session_handler(str(db_symbol))

    def evaluate(self, candles: list, context: StrategyContext) -> Signal:
        params = self._params(context)
        min_required = int(params["min_candles_required"])
        session = self._session(context)

        if len(candles) < min_required:
            return Signal(
                action=SignalAction.HOLD,
                reason=f"INSUFFICIENT_DATA: need {min_required} candles, have {len(candles)}",
            )

        current = candles[-1]
        ts = current.timestamp
        if session.is_market_closed(ts):
            return Signal(action=SignalAction.HOLD, reason="market_closed")

        session_date = session.session_date(ts)
        if session_date is None:
            return Signal(action=SignalAction.HOLD, reason="market_closed")

        if not session.is_opening_range_complete(ts):
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

        opening_range = session.compute_opening_range(candles, session_date)
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

        if high > opening_range.high + min_distance and close <= opening_range.high:
            return Signal(
                action=SignalAction.HOLD,
                reason="waiting_for_breakout",
                metadata=metadata,
            )
        if close > opening_range.high + min_distance:
            opp_key = orb_opportunity_key(
                symbol=str(self._runtime(context).get("db_symbol", "NVDA")),
                session_date=session_date.isoformat(),
                direction="long",
                range_high=opening_range.high,
                range_low=opening_range.low,
                strategy_version=self.version(),
            )
            consumed = set(self._runtime(context).get("consumed_opportunity_keys") or [])
            if opp_key in consumed:
                return Signal(
                    action=SignalAction.HOLD,
                    reason="breakout_already_consumed",
                    metadata={**metadata, "opportunity_key": opp_key},
                )
            sl = close - stop_distance
            risk_per_unit = close - sl
            tp = close + risk_per_unit * Decimal(str(rr))
            return Signal(
                action=SignalAction.BUY,
                reason="breakout_long_confirmed",
                confidence=Decimal("0.65"),
                suggested_sl=sl,
                suggested_tp=tp,
                metadata={**metadata, "opportunity_key": opp_key},
            )

        if low < opening_range.low - min_distance and close >= opening_range.low:
            return Signal(
                action=SignalAction.HOLD,
                reason="waiting_for_breakout",
                metadata=metadata,
            )
        if close < opening_range.low - min_distance:
            opp_key = orb_opportunity_key(
                symbol=str(self._runtime(context).get("db_symbol", "NVDA")),
                session_date=session_date.isoformat(),
                direction="short",
                range_high=opening_range.high,
                range_low=opening_range.low,
                strategy_version=self.version(),
            )
            consumed = set(self._runtime(context).get("consumed_opportunity_keys") or [])
            if opp_key in consumed:
                return Signal(
                    action=SignalAction.HOLD,
                    reason="breakout_already_consumed",
                    metadata={**metadata, "opportunity_key": opp_key},
                )
            sl = close + stop_distance
            risk_per_unit = sl - close
            tp = close - risk_per_unit * Decimal(str(rr))
            return Signal(
                action=SignalAction.SELL,
                reason="breakout_short_confirmed",
                confidence=Decimal("0.65"),
                suggested_sl=sl,
                suggested_tp=tp,
                metadata={**metadata, "opportunity_key": opp_key},
            )

        return Signal(
            action=SignalAction.HOLD,
            reason="waiting_for_breakout",
            metadata=metadata,
        )
