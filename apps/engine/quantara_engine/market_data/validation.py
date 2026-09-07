"""Candle validation."""

from __future__ import annotations

from decimal import Decimal

from quantara_engine.domain.types import Candle


class CandleValidationError(ValueError):
    pass


def validate_candle(candle: Candle) -> None:
    if candle.high < candle.low:
        raise CandleValidationError("high must be >= low")
    if candle.open < candle.low or candle.open > candle.high:
        raise CandleValidationError("open must be within [low, high]")
    if candle.close < candle.low or candle.close > candle.high:
        raise CandleValidationError("close must be within [low, high]")
    for field_name in ("open", "high", "low", "close"):
        value = getattr(candle, field_name)
        if not isinstance(value, Decimal):
            raise CandleValidationError(f"{field_name} must be Decimal")
    if candle.volume is not None and candle.volume < 0:
        raise CandleValidationError("volume must be non-negative")
