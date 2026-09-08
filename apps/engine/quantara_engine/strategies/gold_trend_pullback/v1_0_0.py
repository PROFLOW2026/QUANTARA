"""Gold Trend Pullback Strategy v1.0.0."""

from __future__ import annotations

from decimal import Decimal

from quantara_engine.domain.types import Signal, SignalAction, StrategyContext
from quantara_engine.strategies.base import BaseStrategy
from quantara_engine.strategies.gold_trend_pullback.indicators import (
    atr,
    candles_to_df,
    ema,
    rsi,
    to_decimal,
)


class GoldTrendPullbackV1(BaseStrategy):
    @classmethod
    def strategy_id(cls) -> str:
        return "gold-trend-pullback"

    @classmethod
    def version(cls) -> str:
        return "1.0.0"

    @classmethod
    def name(cls) -> str:
        return "Gold Trend Pullback"

    @classmethod
    def description(cls) -> str:
        return "Trend-following pullback strategy on XAU/USD using EMA and RSI."

    @classmethod
    def supported_instruments(cls) -> list[str]:
        return [
            "XAUUSD",
            "EURUSD",
            "SPY",
            "QQQ",
            "NVDA",
            "AAPL",
            "MSFT",
            "BTCUSD",
        ]

    @classmethod
    def supported_timeframes(cls) -> list[str]:
        return ["1h", "15m", "5m"]

    @classmethod
    def default_parameters(cls) -> dict:
        return {
            "ema_fast": 20,
            "ema_slow": 50,
            "ema_trend": 200,
            "rsi_period": 14,
            "rsi_entry_min": 40,
            "rsi_entry_max": 60,
            "atr_period": 14,
            "atr_sl_multiplier": 1.5,
            "atr_tp_multiplier": 3.0,
            "min_candles_required": 200,
        }

    @classmethod
    def parameters_schema(cls) -> dict:
        return {
            "type": "object",
            "properties": {
                "ema_fast": {"type": "integer", "minimum": 10, "maximum": 30},
                "ema_slow": {"type": "integer", "minimum": 30, "maximum": 100},
                "ema_trend": {"type": "integer", "minimum": 100, "maximum": 300},
                "rsi_period": {"type": "integer", "minimum": 7, "maximum": 21},
                "rsi_entry_min": {"type": "number", "minimum": 30, "maximum": 50},
                "rsi_entry_max": {"type": "number", "minimum": 50, "maximum": 70},
                "atr_period": {"type": "integer", "minimum": 7, "maximum": 21},
                "atr_sl_multiplier": {"type": "number", "minimum": 1.0, "maximum": 3.0},
                "atr_tp_multiplier": {"type": "number", "minimum": 2.0, "maximum": 6.0},
                "min_candles_required": {"type": "integer", "minimum": 150, "maximum": 300},
            },
        }

    @classmethod
    def risk_profile_compatibility(cls) -> list[str]:
        return ["conservative", "balanced", "aggressive"]

    def _params(self, context: StrategyContext) -> dict:
        merged = {**self.default_parameters(), **context.parameters}
        return merged

    def evaluate(self, candles: list, context: StrategyContext) -> Signal:
        params = self._params(context)
        min_required = int(params["min_candles_required"])

        if len(candles) < min_required:
            return Signal(
                action=SignalAction.HOLD,
                reason=f"INSUFFICIENT_DATA: need {min_required} candles, have {len(candles)}",
            )

        df = candles_to_df(candles)
        ema_fast = ema(df["close"], int(params["ema_fast"]))
        ema_slow = ema(df["close"], int(params["ema_slow"]))
        ema_trend = ema(df["close"], int(params["ema_trend"]))
        rsi_vals = rsi(df["close"], int(params["rsi_period"]))
        atr_vals = atr(df, int(params["atr_period"]))

        idx = len(df) - 1
        prev_idx = idx - 1
        close = float(df["close"].iloc[idx])
        low = float(df["low"].iloc[idx])
        high = float(df["high"].iloc[idx])
        ema20 = float(ema_fast.iloc[idx])
        ema50 = float(ema_slow.iloc[idx])
        ema200 = float(ema_trend.iloc[idx])
        rsi_val = float(rsi_vals.iloc[idx])
        atr_val = float(atr_vals.iloc[idx])

        metadata = {
            "ema20": ema20,
            "ema50": ema50,
            "ema200": ema200,
            "rsi": rsi_val,
            "atr": atr_val,
            "effective_parameters": params,
        }

        # Trend reversal CLOSE signals
        if prev_idx >= 0:
            prev_ema50 = float(ema_slow.iloc[prev_idx])
            prev_ema200 = float(ema_trend.iloc[prev_idx])
            if prev_ema50 >= prev_ema200 and ema50 < ema200:
                return Signal(
                    action=SignalAction.CLOSE,
                    reason="TREND_REVERSAL: EMA50 crossed below EMA200",
                    metadata={**metadata, "trend": "down"},
                )
            if prev_ema50 <= prev_ema200 and ema50 > ema200:
                return Signal(
                    action=SignalAction.CLOSE,
                    reason="TREND_REVERSAL: EMA50 crossed above EMA200",
                    metadata={**metadata, "trend": "up"},
                )

        rsi_min = float(params["rsi_entry_min"])
        rsi_max = float(params["rsi_entry_max"])
        sl_mult = float(params["atr_sl_multiplier"])
        tp_mult = float(params["atr_tp_multiplier"])

        # LONG setup
        if close > ema200 and ema50 > ema200:
            metadata["trend"] = "up"
            if low <= ema20 and close > ema20:
                metadata["pullback_detected"] = True
                if rsi_val < rsi_min or rsi_val > rsi_max:
                    return Signal(
                        action=SignalAction.HOLD,
                        reason=f"NO_SETUP: RSI {rsi_val:.1f} outside entry zone [{rsi_min}-{rsi_max}]",
                        metadata=metadata,
                    )
                sl = to_decimal(close - atr_val * sl_mult)
                tp = to_decimal(close + atr_val * tp_mult)
                return Signal(
                    action=SignalAction.BUY,
                    reason="Pullback to EMA20 in uptrend, RSI confirmed",
                    confidence=Decimal("0.72"),
                    suggested_sl=sl,
                    suggested_tp=tp,
                    metadata=metadata,
                )
            return Signal(
                action=SignalAction.HOLD,
                reason="HOLD: trend valid, no pullback yet",
                metadata=metadata,
            )

        # SHORT setup
        if close < ema200 and ema50 < ema200:
            metadata["trend"] = "down"
            if high >= ema20 and close < ema20:
                metadata["pullback_detected"] = True
                if rsi_val < rsi_min or rsi_val > rsi_max:
                    return Signal(
                        action=SignalAction.HOLD,
                        reason=f"NO_SETUP: RSI {rsi_val:.1f} outside entry zone [{rsi_min}-{rsi_max}]",
                        metadata=metadata,
                    )
                sl = to_decimal(close + atr_val * sl_mult)
                tp = to_decimal(close - atr_val * tp_mult)
                return Signal(
                    action=SignalAction.SELL,
                    reason="Rally to EMA20 in downtrend, RSI confirmed",
                    confidence=Decimal("0.72"),
                    suggested_sl=sl,
                    suggested_tp=tp,
                    metadata=metadata,
                )
            return Signal(
                action=SignalAction.HOLD,
                reason="HOLD: downtrend valid, no rally yet",
                metadata=metadata,
            )

        if close <= ema200:
            return Signal(
                action=SignalAction.HOLD,
                reason="NO_SETUP: price below EMA200, no long setup",
                metadata=metadata,
            )

        return Signal(
            action=SignalAction.HOLD,
            reason="NO_SETUP: trend unclear",
            metadata=metadata,
        )
