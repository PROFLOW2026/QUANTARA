"""P&L calculations — canonical account-currency (USD) model."""

from __future__ import annotations

from decimal import Decimal

from quantara_engine.domain.types import Direction, Instrument, Position
from quantara_engine.portfolio.currency import ACCOUNT_CURRENCY, CurrencyContext, FxRateTable


def raw_pnl_in_quote(
    direction: Direction,
    entry_fill_price: Decimal,
    mark_or_exit_price: Decimal,
    quantity: Decimal,
) -> Decimal:
    if direction == Direction.LONG:
        return ((mark_or_exit_price - entry_fill_price) * quantity).quantize(Decimal("0.01"))
    return ((entry_fill_price - mark_or_exit_price) * quantity).quantize(Decimal("0.01"))


def convert_quote_pnl_to_account(
    pnl_quote: Decimal,
    quote_currency: str,
    fx_rates: FxRateTable,
) -> Decimal:
    return fx_rates.quote_to_account(pnl_quote, quote_currency)


def unrealized_pnl(
    direction: Direction,
    entry_fill_price: Decimal,
    current_price: Decimal,
    quantity: Decimal,
    instrument: Instrument,
    fx_rates: FxRateTable,
) -> Decimal:
    raw = raw_pnl_in_quote(direction, entry_fill_price, current_price, quantity)
    return convert_quote_pnl_to_account(raw, instrument.quote_currency, fx_rates)


def gross_pnl(
    direction: Direction,
    entry_fill_price: Decimal,
    exit_fill_price: Decimal,
    quantity: Decimal,
    instrument: Instrument,
    fx_rates: FxRateTable,
) -> Decimal:
    raw = raw_pnl_in_quote(direction, entry_fill_price, exit_fill_price, quantity)
    return convert_quote_pnl_to_account(raw, instrument.quote_currency, fx_rates)


def net_pnl(gross: Decimal, entry_fees: Decimal, exit_fees: Decimal) -> Decimal:
    return (gross - entry_fees - exit_fees).quantize(Decimal("0.01"))


def update_position_unrealized(
    position: Position,
    mark_price: Decimal,
    instrument: Instrument,
    fx_rates: FxRateTable,
) -> Decimal:
    pnl = unrealized_pnl(
        position.direction,
        position.entry_price,
        mark_price,
        position.quantity,
        instrument,
        fx_rates,
    )
    position.current_price = mark_price
    position.unrealized_pnl = pnl
    return pnl


def exposure_notional_account(
    positions: list[Position],
    mark_prices: dict[str, Decimal] | Decimal,
    currency: CurrencyContext,
) -> Decimal:
    total = Decimal("0")
    for pos in positions:
        inst = currency.instrument_for(pos)
        if isinstance(mark_prices, dict):
            mark = mark_prices.get(pos.instrument_id, pos.current_price)
        else:
            mark = mark_prices
        total += currency.fx_rates.quote_notional_to_account(
            pos.quantity, mark, inst.quote_currency
        )
    return total.quantize(Decimal("0.01"))
