"""Stop/target trigger detection for paper and backtest execution.

Open-first rule: evaluate candle.open against SL/TP before intrabar high/low.
Gap exits use the tradable open price (not the resting trigger level).

When both SL and TP are touched inside the same OHLC candle (and open did not
already resolve the exit), stop loss wins (conservative — intrabar order unknown).
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from quantara_engine.domain.types import ExitReason

if TYPE_CHECKING:
    from quantara_engine.domain.types import Candle, Position


def _open_crosses_sl(position: Position, candle_open: Decimal) -> bool:
    if position.direction.value == "long":
        return candle_open <= position.stop_loss
    return candle_open >= position.stop_loss


def _open_crosses_tp(position: Position, candle_open: Decimal) -> bool:
    if position.take_profit is None:
        return False
    if position.direction.value == "long":
        return candle_open >= position.take_profit
    return candle_open <= position.take_profit


def detect_exit_trigger(
    position: Position,
    candle: Candle,
) -> tuple[ExitReason, Decimal] | None:
    """Return (exit_reason, execution_base_price) if SL or TP hit on this candle."""
    open_px = candle.open

    if _open_crosses_sl(position, open_px):
        return ExitReason.SL, open_px
    if _open_crosses_tp(position, open_px):
        return ExitReason.TP, open_px

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
