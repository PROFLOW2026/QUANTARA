"""Robot A RSI shadow variants — observation only, never enters active pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal

from quantara_engine.domain.types import Signal, SignalAction
from quantara_engine.strategies.gold_trend_pullback.indicators import (
    atr,
    candles_to_df,
    ema,
    rsi,
    to_decimal,
)

ShadowVariant = Literal["active", "directional", "no_rsi"]


@dataclass(frozen=True)
class ShadowSignalResult:
    action: str
    reason: str
    rsi: float | None = None
    ema20: float | None = None
    ema50: float | None = None
    ema200: float | None = None
    atr: float | None = None
    suggested_sl: Decimal | None = None
    suggested_tp: Decimal | None = None
    metadata: dict[str, Any] | None = None


def _action_name(action: SignalAction | str) -> str:
    if isinstance(action, SignalAction):
        return action.value.upper() if hasattr(action, "value") else str(action).upper()
    return str(action).upper().replace("SIGNALACTION.", "")


def _normalize_active(signal: Signal) -> ShadowSignalResult:
    meta = signal.metadata or {}
    return ShadowSignalResult(
        action=_action_name(signal.action),
        reason=signal.reason or "",
        rsi=float(meta["rsi"]) if meta.get("rsi") is not None else None,
        ema20=float(meta["ema20"]) if meta.get("ema20") is not None else None,
        ema50=float(meta["ema50"]) if meta.get("ema50") is not None else None,
        ema200=float(meta["ema200"]) if meta.get("ema200") is not None else None,
        atr=float(meta["atr"]) if meta.get("atr") is not None else None,
        suggested_sl=signal.suggested_sl,
        suggested_tp=signal.suggested_tp,
        metadata=dict(meta),
    )


def evaluate_shadow_variant(
    candles: list,
    params: dict[str, Any],
    *,
    variant: ShadowVariant,
) -> ShadowSignalResult:
    """Replicate Robot A logic with RSI filter variants. Does not mutate params."""
    min_required = int(params.get("min_candles_required", 200))
    if len(candles) < min_required:
        return ShadowSignalResult(
            action="HOLD",
            reason=f"INSUFFICIENT_DATA: need {min_required} candles, have {len(candles)}",
        )

    df = candles_to_df(candles)
    ema_fast = ema(df["close"], int(params.get("ema_fast", 20)))
    ema_slow = ema(df["close"], int(params.get("ema_slow", 50)))
    ema_trend = ema(df["close"], int(params.get("ema_trend", 200)))
    rsi_vals = rsi(df["close"], int(params.get("rsi_period", 14)))
    atr_vals = atr(df, int(params.get("atr_period", 14)))

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
        "shadow_variant": variant,
    }

    if prev_idx >= 0:
        prev_ema50 = float(ema_slow.iloc[prev_idx])
        prev_ema200 = float(ema_trend.iloc[prev_idx])
        if prev_ema50 >= prev_ema200 and ema50 < ema200:
            return ShadowSignalResult(
                action="CLOSE",
                reason="TREND_REVERSAL: EMA50 crossed below EMA200",
                rsi=rsi_val,
                ema20=ema20,
                ema50=ema50,
                ema200=ema200,
                atr=atr_val,
                metadata={**metadata, "trend": "down"},
            )
        if prev_ema50 <= prev_ema200 and ema50 > ema200:
            return ShadowSignalResult(
                action="CLOSE",
                reason="TREND_REVERSAL: EMA50 crossed above EMA200",
                rsi=rsi_val,
                ema20=ema20,
                ema50=ema50,
                ema200=ema200,
                atr=atr_val,
                metadata={**metadata, "trend": "up"},
            )

    rsi_min = float(params.get("rsi_entry_min", 40))
    rsi_max = float(params.get("rsi_entry_max", 60))
    sl_mult = float(params.get("atr_sl_multiplier", 1.5))
    tp_mult = float(params.get("atr_tp_multiplier", 3.0))

    def _rsi_ok_long() -> tuple[bool, str | None]:
        if variant == "no_rsi":
            return True, None
        if variant == "directional":
            if rsi_val < rsi_min:
                return False, f"NO_SETUP: RSI {rsi_val:.1f} below directional floor [{rsi_min}]"
            return True, None
        # active 40–60
        if rsi_val < rsi_min or rsi_val > rsi_max:
            return False, f"NO_SETUP: RSI {rsi_val:.1f} outside entry zone [{rsi_min}-{rsi_max}]"
        return True, None

    def _rsi_ok_short() -> tuple[bool, str | None]:
        if variant == "no_rsi":
            return True, None
        if variant == "directional":
            if rsi_val > rsi_max:
                return False, f"NO_SETUP: RSI {rsi_val:.1f} above directional cap [{rsi_max}]"
            return True, None
        if rsi_val < rsi_min or rsi_val > rsi_max:
            return False, f"NO_SETUP: RSI {rsi_val:.1f} outside entry zone [{rsi_min}-{rsi_max}]"
        return True, None

    if close > ema200 and ema50 > ema200:
        metadata["trend"] = "up"
        if low <= ema20 and close > ema20:
            metadata["pullback_detected"] = True
            ok, deny = _rsi_ok_long()
            if not ok:
                return ShadowSignalResult(
                    action="HOLD",
                    reason=deny or "NO_SETUP: RSI filter",
                    rsi=rsi_val,
                    ema20=ema20,
                    ema50=ema50,
                    ema200=ema200,
                    atr=atr_val,
                    metadata=metadata,
                )
            sl = to_decimal(close - atr_val * sl_mult)
            tp = to_decimal(close + atr_val * tp_mult)
            return ShadowSignalResult(
                action="BUY",
                reason="Pullback to EMA20 in uptrend, RSI confirmed"
                if variant == "active"
                else (
                    "Pullback to EMA20 in uptrend, directional RSI"
                    if variant == "directional"
                    else "Pullback to EMA20 in uptrend, no RSI filter"
                ),
                rsi=rsi_val,
                ema20=ema20,
                ema50=ema50,
                ema200=ema200,
                atr=atr_val,
                suggested_sl=sl,
                suggested_tp=tp,
                metadata=metadata,
            )
        return ShadowSignalResult(
            action="HOLD",
            reason="HOLD: trend valid, no pullback yet",
            rsi=rsi_val,
            ema20=ema20,
            ema50=ema50,
            ema200=ema200,
            atr=atr_val,
            metadata=metadata,
        )

    if close < ema200 and ema50 < ema200:
        metadata["trend"] = "down"
        if high >= ema20 and close < ema20:
            metadata["pullback_detected"] = True
            ok, deny = _rsi_ok_short()
            if not ok:
                return ShadowSignalResult(
                    action="HOLD",
                    reason=deny or "NO_SETUP: RSI filter",
                    rsi=rsi_val,
                    ema20=ema20,
                    ema50=ema50,
                    ema200=ema200,
                    atr=atr_val,
                    metadata=metadata,
                )
            sl = to_decimal(close + atr_val * sl_mult)
            tp = to_decimal(close - atr_val * tp_mult)
            return ShadowSignalResult(
                action="SELL",
                reason="Rally to EMA20 in downtrend, RSI confirmed"
                if variant == "active"
                else (
                    "Rally to EMA20 in downtrend, directional RSI"
                    if variant == "directional"
                    else "Rally to EMA20 in downtrend, no RSI filter"
                ),
                rsi=rsi_val,
                ema20=ema20,
                ema50=ema50,
                ema200=ema200,
                atr=atr_val,
                suggested_sl=sl,
                suggested_tp=tp,
                metadata=metadata,
            )
        return ShadowSignalResult(
            action="HOLD",
            reason="HOLD: downtrend valid, no rally yet",
            rsi=rsi_val,
            ema20=ema20,
            ema50=ema50,
            ema200=ema200,
            atr=atr_val,
            metadata=metadata,
        )

    if close <= ema200:
        return ShadowSignalResult(
            action="HOLD",
            reason="NO_SETUP: price below EMA200, no long setup",
            rsi=rsi_val,
            ema20=ema20,
            ema50=ema50,
            ema200=ema200,
            atr=atr_val,
            metadata=metadata,
        )

    return ShadowSignalResult(
        action="HOLD",
        reason="NO_SETUP: trend unclear",
        rsi=rsi_val,
        ema20=ema20,
        ema50=ema50,
        ema200=ema200,
        atr=atr_val,
        metadata=metadata,
    )


def evaluate_all_rsi_shadows(
    candles: list,
    params: dict[str, Any],
    *,
    active_signal: Signal | None = None,
) -> dict[str, ShadowSignalResult]:
    """Return active + two shadow variants. Active prefers real pipeline signal when provided."""
    active = (
        _normalize_active(active_signal)
        if active_signal is not None
        else evaluate_shadow_variant(candles, params, variant="active")
    )
    return {
        "active": active,
        "directional": evaluate_shadow_variant(candles, params, variant="directional"),
        "no_rsi": evaluate_shadow_variant(candles, params, variant="no_rsi"),
    }
