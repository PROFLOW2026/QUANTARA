"""Volatility Squeeze / Expansion Strategy v1.0.0 — Robot D."""

from __future__ import annotations

from decimal import Decimal

from quantara_engine.domain.types import Signal, SignalAction, StrategyContext
from quantara_engine.market_data.active_universe import list_active_db_symbols
from quantara_engine.strategies.base import BaseStrategy
from quantara_engine.strategies.common.indicators import (
    atr,
    bollinger_bands,
    candles_to_df,
    keltner_channels,
    percentile_rank,
    session_open_for_asset,
    to_decimal,
)


class VolatilitySqueezeV1(BaseStrategy):
    @classmethod
    def strategy_id(cls) -> str:
        return "volatility-squeeze"

    @classmethod
    def version(cls) -> str:
        return "1.0.0"

    @classmethod
    def name(cls) -> str:
        return "Volatility Squeeze"

    @classmethod
    def description(cls) -> str:
        return (
            "Detects volatility compression via Bollinger/Keltner squeeze or low BB width "
            "percentile, then trades confirmed expansion breakouts on 15m."
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
            "bb_period": 20,
            "bb_std": 2.0,
            "keltner_period": 20,
            "keltner_atr_mult": 2.0,
            "atr_period": 14,
            "bb_width_lookback": 100,
            "bb_width_percentile_max": 20.0,
            "compression_min_bars": 5,
            "atr_sl_multiplier": 1.5,
            "reward_risk_ratio": 2.0,
            "expansion_atr_mult": 1.1,
            "max_holding_bars": 16,
            "min_candles_required": 120,
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

    def _compression_window(self, df, params: dict, idx: int) -> tuple[bool, int]:
        bb_upper, _, bb_lower = bollinger_bands(
            df["close"], int(params["bb_period"]), float(params["bb_std"])
        )
        kc_upper, _, kc_lower = keltner_channels(
            df,
            int(params["keltner_period"]),
            float(params["keltner_atr_mult"]),
            int(params["atr_period"]),
        )
        bb_width = (bb_upper - bb_lower) / df["close"].replace(0, float("nan"))
        width_pct = percentile_rank(bb_width, int(params["bb_width_lookback"]))

        min_bars = int(params["compression_min_bars"])
        width_max = float(params["bb_width_percentile_max"])
        squeeze_count = 0
        for i in range(idx, max(idx - min_bars, -1), -1):
            if i < 0:
                break
            inside_keltner = (
                float(bb_upper.iloc[i]) <= float(kc_upper.iloc[i])
                and float(bb_lower.iloc[i]) >= float(kc_lower.iloc[i])
            )
            low_width = float(width_pct.iloc[i]) <= width_max if not pd_isna(width_pct.iloc[i]) else False
            if inside_keltner or low_width:
                squeeze_count += 1
            else:
                break
        return squeeze_count >= min_bars, squeeze_count

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
                    reason="time_stop_failed_expansion",
                    metadata={"bars_held": bars_held},
                )
            return Signal(action=SignalAction.HOLD, reason="hold_open_position")

        df = candles_to_df(candles)
        idx = len(df) - 1
        close = float(df["close"].iloc[idx])
        high = float(df["high"].iloc[idx])
        low = float(df["low"].iloc[idx])

        bb_upper, _, bb_lower = bollinger_bands(
            df["close"], int(params["bb_period"]), float(params["bb_std"])
        )
        range_high = float(bb_upper.iloc[idx - 1]) if idx > 0 else float(bb_upper.iloc[idx])
        range_low = float(bb_lower.iloc[idx - 1]) if idx > 0 else float(bb_lower.iloc[idx])

        atr_vals = atr(df, int(params["atr_period"]))
        atr_val = float(atr_vals.iloc[idx])
        prev_atr = float(atr_vals.iloc[idx - 1]) if idx > 0 else atr_val
        tr_now = high - low

        compressed, compression_bars = self._compression_window(df, params, idx - 1)
        metadata = {
            "compression_bars": compression_bars,
            "compressed": compressed,
            "atr": atr_val,
            "range_high": range_high,
            "range_low": range_low,
            "effective_parameters": params,
        }

        if not compressed:
            return Signal(
                action=SignalAction.HOLD,
                reason="no_compression_detected",
                metadata=metadata,
            )

        expansion_mult = float(params["expansion_atr_mult"])
        if tr_now < prev_atr * expansion_mult and tr_now < atr_val * expansion_mult:
            return Signal(
                action=SignalAction.HOLD,
                reason="compression_no_breakout_yet",
                metadata=metadata,
            )

        sl_mult = float(params["atr_sl_multiplier"])
        rr = float(params["reward_risk_ratio"])

        if close > range_high and high > range_high:
            sl = to_decimal(close - atr_val * sl_mult)
            risk = close - float(sl)
            tp = to_decimal(close + risk * rr)
            return Signal(
                action=SignalAction.BUY,
                reason="squeeze_release_long",
                confidence=Decimal("0.7"),
                suggested_sl=sl,
                suggested_tp=tp,
                metadata=metadata,
            )

        if close < range_low and low < range_low:
            sl = to_decimal(close + atr_val * sl_mult)
            risk = float(sl) - close
            tp = to_decimal(close - risk * rr)
            return Signal(
                action=SignalAction.SELL,
                reason="squeeze_release_short",
                confidence=Decimal("0.7"),
                suggested_sl=sl,
                suggested_tp=tp,
                metadata=metadata,
            )

        return Signal(
            action=SignalAction.HOLD,
            reason="compression_no_breakout_yet",
            metadata=metadata,
        )


def pd_isna(value) -> bool:
    import math

    try:
        return math.isnan(float(value))
    except (TypeError, ValueError):
        return True
