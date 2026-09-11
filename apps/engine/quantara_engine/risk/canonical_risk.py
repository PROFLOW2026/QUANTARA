"""Canonical expected loss-to-SL in account currency (executable fills)."""

from __future__ import annotations

from decimal import Decimal

from quantara_engine.domain.types import Direction, ExecutionAssumptions, Instrument
from quantara_engine.execution.fill_calculator import calculate_fill_price
from quantara_engine.portfolio.currency import FxRateTable


def quote_pnl(
    direction: Direction,
    entry_price: Decimal,
    exit_price: Decimal,
    quantity: Decimal,
) -> Decimal:
    if direction == Direction.LONG:
        return (exit_price - entry_price) * quantity
    return (entry_price - exit_price) * quantity


def expected_loss_to_sl_usd(
    *,
    direction: Direction,
    entry_base: Decimal,
    stop_loss: Decimal,
    quantity: Decimal,
    instrument: Instrument,
    assumptions: ExecutionAssumptions,
    fx_rates: FxRateTable,
) -> Decimal:
    """Expected account-currency loss if SL hit using executable entry/exit fills."""
    entry = calculate_fill_price(direction, "entry", entry_base, quantity, assumptions)
    sl = calculate_fill_price(direction, "exit", stop_loss, quantity, assumptions)
    loss_quote = quote_pnl(direction, entry.fill_price, sl.fill_price, quantity)
    return fx_rates.quote_to_account(abs(loss_quote), instrument.quote_currency)


def mid_price_loss_to_sl_usd(
    *,
    direction: Direction,
    entry_price: Decimal,
    stop_loss: Decimal,
    quantity: Decimal,
    instrument: Instrument,
    fx_rates: FxRateTable,
) -> Decimal:
    """Mid-price entry→SL loss (legacy forensic method — not for guards)."""
    from quantara_engine.portfolio.pnl import raw_pnl_in_quote

    raw = raw_pnl_in_quote(direction, entry_price, stop_loss, quantity)
    return abs(fx_rates.quote_to_account(raw, instrument.quote_currency))
