"""Momentum Continuation Strategy v1.0.0 — Robot E."""

from __future__ import annotations

from decimal import Decimal

from quantara_engine.domain.types import Candle, Signal, SignalAction, StrategyContext
from quantara_engine.market_data.active_universe import list_active_db_symbols
from quantara_engine.strategies.base import BaseStrategy
from quantara_engine.strategies.common.indicators import (
    _directional_system,
    atr,
    candles_to_df,
    ema,
    ema_slope,
    normalized_momentum,
    rsi,
    session_open_for_asset,
    to_decimal,
)


class MomentumContinuationV1(BaseStrategy):
    @classmethod
    def strategy_id(cls) -> str:
        return "momentum-continuation"

    @classmethod
    def version(cls) -> str:
        return "1.0.0"

    @classmethod
    def name(cls) -> str:
        return "Momentum Continuation"

    @classmethod
    def description(cls) -> str:
        return (
            "Trend momentum continuation on 15m with 1h bias confirmation — "
            "enters established moves with ADX strength without late overextension."
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
            "ema_fast": 20,
            "ema_slow": 50,
            "rsi_period": 14,
            "atr_period": 14,
            "adx_period": 14,
            "adx_min": 25.0,
            "rsi_long_min": 58.0,
            "rsi_long_max": 72.0,
            "rsi_short_min": 28.0,
            "rsi_short_max": 42.0,
            "momentum_bars": 5,
            "momentum_min_atr": 1.0,
            "max_extension_atr": 1.5,
            "atr_sl_multiplier": 1.5,
            "reward_risk_ratio": 2.5,
            "max_holding_bars": 20,
            "min_candles_required": 60,
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

    def _closed_1h_candles(self, runtime: dict, signal_ts) -> list[Candle]:
        raw = runtime.get("confirmation_candles_1h") or []
        closed = [c for c in raw if c.timestamp <= signal_ts]
        return closed

    def _hourly_bias(self, candles_1h: list, params: dict) -> str | None:
        if len(candles_1h) < int(params["ema_slow"]) + 2:
            return None
        df = candles_to_df(candles_1h)
        idx = len(df) - 1
        ema_fast = ema(df["close"], int(params["ema_fast"]))
        ema_slow = ema(df["close"], int(params["ema_slow"]))
        if float(ema_fast.iloc[idx]) > float(ema_slow.iloc[idx]):
            return "up"
        if float(ema_fast.iloc[idx]) < float(ema_slow.iloc[idx]):
            return "down"
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

        if runtime.get("has_open_position"):
            bars_held = int((runtime.get("open_position") or {}).get("bars_held") or 0)
            if bars_held >= int(params["max_holding_bars"]):
                return Signal(
                    action=SignalAction.CLOSE,
                    reason="time_stop_momentum_faded",
                    metadata={"bars_held": bars_held},
                )
            return Signal(action=SignalAction.HOLD, reason="hold_open_position")

        df = candles_to_df(candles)
        idx = len(df) - 1
        close = float(df["close"].iloc[idx])

        ema_fast = ema(df["close"], int(params["ema_fast"]))
        ema_slow = ema(df["close"], int(params["ema_slow"]))
        ema20 = float(ema_fast.iloc[idx])
        ema50 = float(ema_slow.iloc[idx])
        fast_slope = float(ema_slope(ema_fast, 3).iloc[idx])
        slow_slope = float(ema_slope(ema_slow, 3).iloc[idx])

        rsi_vals = rsi(df["close"], int(params["rsi_period"]))
        rsi_val = float(rsi_vals.iloc[idx])

        atr_vals = atr(df, int(params["atr_period"]))
        atr_val = float(atr_vals.iloc[idx])

        _, _, adx_vals = _directional_system(df, int(params["adx_period"]))
        adx_val = float(adx_vals.iloc[idx])

        mom = normalized_momentum(df, int(params["momentum_bars"]), int(params["atr_period"]))
        mom_val = float(mom.iloc[idx])

        extension = abs(close - ema20) / atr_val if atr_val > 0 else 0.0
        metadata = {
            "ema20": ema20,
            "ema50": ema50,
            "rsi": rsi_val,
            "adx": adx_val,
            "momentum_atr": mom_val,
            "extension_atr": extension,
            "effective_parameters": params,
        }

        candles_1h = self._closed_1h_candles(runtime, current.timestamp)
        bias = self._hourly_bias(candles_1h, params)
        metadata["hourly_bias"] = bias
        if bias is None:
            return Signal(
                action=SignalAction.HOLD,
                reason="no_hourly_confirmation",
                metadata=metadata,
            )

        adx_min = float(params["adx_min"])
        max_ext = float(params["max_extension_atr"])
        mom_min = float(params["momentum_min_atr"])
        sl_mult = float(params["atr_sl_multiplier"])
        rr = float(params["reward_risk_ratio"])

        if extension > max_ext:
            return Signal(
                action=SignalAction.HOLD,
                reason="overextended_from_mean",
                metadata=metadata,
            )

        if adx_val < adx_min:
            return Signal(
                action=SignalAction.HOLD,
                reason="insufficient_directional_strength",
                metadata=metadata,
            )

        # Long momentum
        if (
            bias == "up"
            and ema20 > ema50
            and fast_slope > 0
            and slow_slope > 0
            and float(params["rsi_long_min"]) <= rsi_val <= float(params["rsi_long_max"])
            and mom_val >= mom_min
        ):
            sl = to_decimal(close - atr_val * sl_mult)
            risk = close - float(sl)
            tp = to_decimal(close + risk * rr)
            return Signal(
                action=SignalAction.BUY,
                reason="momentum_long_confirmed",
                confidence=Decimal("0.74"),
                suggested_sl=sl,
                suggested_tp=tp,
                metadata=metadata,
            )

        # Short momentum
        if (
            bias == "down"
            and ema20 < ema50
            and fast_slope < 0
            and slow_slope < 0
            and float(params["rsi_short_min"]) <= rsi_val <= float(params["rsi_short_max"])
            and mom_val <= -mom_min
        ):
            sl = to_decimal(close + atr_val * sl_mult)
            risk = float(sl) - close
            tp = to_decimal(close - risk * rr)
            return Signal(
                action=SignalAction.SELL,
                reason="momentum_short_confirmed",
                confidence=Decimal("0.74"),
                suggested_sl=sl,
                suggested_tp=tp,
                metadata=metadata,
            )

        return Signal(action=SignalAction.HOLD, reason="no_momentum_setup", metadata=metadata)
