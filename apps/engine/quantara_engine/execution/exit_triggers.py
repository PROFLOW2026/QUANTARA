"""Stop/target trigger detection for paper and backtest execution.

Paper/backtest semantics: when both SL and TP are touched within the same OHLC
candle, stop loss wins (conservative rule — intrabar order is unknown).
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from quantara_engine.domain.types import ExitReason

if TYPE_CHECKING:
    from quantara_engine.domain.types import Candle, Position


def detect_exit_trigger(
    position: Position,
    candle: Candle,
) -> tuple[ExitReason, Decimal] | None:
    """Return (exit_reason, trigger_price) if SL or TP hit on this candle."""
    if position.direction.value == "long":
        sl_hit = candle.low <= position.stop_loss
        tp_hit = position.take_profit is not None and candle.high >= position.take_profit
    else:
        sl_hit = candle.high >= position.stop_loss
        tp_hit = position.take_profit is not None and candle.low <= position.take_profit

    if sl_hit:
        return ExitReason.SL, position.stop_loss
    if tp_hit and position.take_profit is not None:
        return ExitReason.TP, position.take_profit
    return None


def find_first_exit_candle(
    position: Position,
    candles: list[Candle],
    *,
    after_timestamp=None,
) -> tuple[Candle, ExitReason, Decimal] | None:
    """Scan completed candles chronologically for the first SL/TP trigger."""
    for candle in candles:
        if after_timestamp is not None and candle.timestamp <= after_timestamp:
            continue
        trigger = detect_exit_trigger(position, candle)
        if trigger:
            reason, price = trigger
            return candle, reason, price
    return None
