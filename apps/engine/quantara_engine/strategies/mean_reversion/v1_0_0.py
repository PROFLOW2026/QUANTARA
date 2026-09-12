"""Mean Reversion Strategy v1.0.0 — Robot C."""

from __future__ import annotations

from decimal import Decimal

from quantara_engine.domain.types import Signal, SignalAction, StrategyContext
from quantara_engine.market_data.active_universe import list_active_db_symbols
from quantara_engine.strategies.base import BaseStrategy
from quantara_engine.strategies.common.indicators import (
    _directional_system,
    atr,
    bollinger_bands,
    candles_to_df,
    ema,
    rsi,
    session_open_for_asset,
    to_decimal,
)


class MeanReversionV1(BaseStrategy):
    @classmethod
    def strategy_id(cls) -> str:
        return "mean-reversion"

    @classmethod
    def version(cls) -> str:
        return "1.0.0"

    @classmethod
    def name(cls) -> str:
        return "Mean Reversion"

    @classmethod
    def description(cls) -> str:
        return (
            "Range-bound mean reversion on 15m — enters when price stretches from EMA "
            "with low ADX and RSI extremes; targets return to mean."
        )

    @classmethod
    def supported_instruments(cls) -> list[str]:
        return list(list_active_db_symbols())

    @classmethod
    def supported_timeframes(cls) -> list[str]:
        return ["15m"]

    @classmethod
    def default_parameters(cls) -> dict:
        return {
            "ema_period": 20,
            "bb_period": 20,
            "bb_std": 2.0,
            "rsi_period": 14,
            "atr_period": 14,
            "adx_period": 14,
            "adx_max": 22.0,
            "rsi_oversold": 35.0,
            "rsi_overbought": 65.0,
            "min_stretch_atr": 1.25,
            "atr_sl_multiplier": 1.5,
            "min_reward_risk": 1.0,
            "max_holding_bars": 12,
            "adx_exit_threshold": 28.0,
            "min_candles_required": 50,
        }

    @classmethod
    def parameters_schema(cls) -> dict:
        return {"type": "object", "properties": cls.default_parameters()}

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

    def _position_runtime(self, context: StrategyContext) -> dict:
        return dict(context.runtime.get("open_position") or {})

    def _manage_open_position(
        self,
        *,
        context: StrategyContext,
        adx_val: float,
        plus_di: float,
        minus_di: float,
        metadata: dict,
    ) -> Signal | None:
        pos = self._position_runtime(context)
        if not pos:
            return None
        params = self._params(context)
        bars_held = int(pos.get("bars_held") or 0)
        direction = str(pos.get("direction") or "")
        max_bars = int(params["max_holding_bars"])
        adx_exit = float(params["adx_exit_threshold"])

        if bars_held >= max_bars:
            return Signal(
                action=SignalAction.CLOSE,
                reason="time_stop_max_holding",
                metadata={**metadata, "bars_held": bars_held},
            )

        if adx_val >= adx_exit:
            if direction == "long" and minus_di > plus_di:
                return Signal(
                    action=SignalAction.CLOSE,
                    reason="strong_trend_against_long",
                    metadata={**metadata, "adx": adx_val},
                )
            if direction == "short" and plus_di > minus_di:
                return Signal(
                    action=SignalAction.CLOSE,
                    reason="strong_trend_against_short",
                    metadata={**metadata, "adx": adx_val},
                )
        return None

    def evaluate(self, candles: list, context: StrategyContext) -> Signal:
        params = self._params(context)
        min_required = int(params["min_candles_required"])
        runtime = context.runtime or {}
        db_symbol = str(runtime.get("db_symbol") or "")

        if len(candles) < min_required:
            return Signal(
                action=SignalAction.HOLD,
                reason="insufficient_data",
                metadata={"have": len(candles), "need": min_required},
            )

        current = candles[-1]
        if not session_open_for_asset(db_symbol, current.timestamp):
            return Signal(action=SignalAction.HOLD, reason="market_closed")

        df = candles_to_df(candles)
        idx = len(df) - 1
        close = float(df["close"].iloc[idx])
        low = float(df["low"].iloc[idx])
        high = float(df["high"].iloc[idx])

        ema_period = int(params["ema_period"])
        ema_vals = ema(df["close"], ema_period)
        ema20 = float(ema_vals.iloc[idx])

        rsi_vals = rsi(df["close"], int(params["rsi_period"]))
        rsi_val = float(rsi_vals.iloc[idx])

        atr_vals = atr(df, int(params["atr_period"]))
        atr_val = float(atr_vals.iloc[idx])
        if atr_val <= 0 or not np_finite(atr_val):
            return Signal(action=SignalAction.HOLD, reason="invalid_atr")

        bb_upper, bb_middle, bb_lower = bollinger_bands(
            df["close"], int(params["bb_period"]), float(params["bb_std"])
        )
        bb_low = float(bb_lower.iloc[idx])
        bb_up = float(bb_upper.iloc[idx])

        plus_di_s, minus_di_s, adx_vals = _directional_system(df, int(params["adx_period"]))
        adx_val = float(adx_vals.iloc[idx])
        plus_di = float(plus_di_s.iloc[idx])
        minus_di = float(minus_di_s.iloc[idx])

        stretch = abs(close - ema20) / atr_val
        metadata = {
            "ema20": ema20,
            "rsi": rsi_val,
            "atr": atr_val,
            "adx": adx_val,
            "stretch_atr": stretch,
            "bb_lower": bb_low,
            "bb_upper": bb_up,
            "effective_parameters": params,
        }

        if runtime.get("has_open_position"):
            managed = self._manage_open_position(
                context=context,
                adx_val=adx_val,
                plus_di=plus_di,
                minus_di=minus_di,
                metadata=metadata,
            )
            if managed:
                return managed
            return Signal(action=SignalAction.HOLD, reason="hold_open_position", metadata=metadata)

        adx_max = float(params["adx_max"])
        if adx_val > adx_max:
            return Signal(
                action=SignalAction.HOLD,
                reason="strong_trend_no_mean_reversion",
                metadata=metadata,
            )

        min_stretch = float(params["min_stretch_atr"])
        sl_mult = float(params["atr_sl_multiplier"])
        min_rr = float(params["min_reward_risk"])
        rsi_os = float(params["rsi_oversold"])
        rsi_ob = float(params["rsi_overbought"])

        # Long — stretched below mean
        if rsi_val <= rsi_os and (low <= bb_low or close < ema20) and stretch >= min_stretch:
            sl = to_decimal(close - atr_val * sl_mult)
            tp = to_decimal(ema20)
            risk = close - float(sl)
            reward = float(tp) - close
            if risk <= 0 or reward / risk < min_rr:
                return Signal(
                    action=SignalAction.HOLD,
                    reason="insufficient_reward_to_mean",
                    metadata={**metadata, "reward_risk": reward / risk if risk > 0 else 0},
                )
            return Signal(
                action=SignalAction.BUY,
                reason="long_stretch_to_mean",
                confidence=Decimal("0.68"),
                suggested_sl=sl,
                suggested_tp=tp,
                metadata=metadata,
            )

        # Short — stretched above mean
        if rsi_val >= rsi_ob and (high >= bb_up or close > ema20) and stretch >= min_stretch:
            sl = to_decimal(close + atr_val * sl_mult)
            tp = to_decimal(ema20)
            risk = float(sl) - close
            reward = close - float(tp)
            if risk <= 0 or reward / risk < min_rr:
                return Signal(
                    action=SignalAction.HOLD,
                    reason="insufficient_reward_to_mean",
                    metadata={**metadata, "reward_risk": reward / risk if risk > 0 else 0},
                )
            return Signal(
                action=SignalAction.SELL,
                reason="short_stretch_to_mean",
                confidence=Decimal("0.68"),
                suggested_sl=sl,
                suggested_tp=tp,
                metadata=metadata,
            )

        return Signal(action=SignalAction.HOLD, reason="no_mean_reversion_setup", metadata=metadata)


def np_finite(value: float) -> bool:
    import math

    return math.isfinite(value)

