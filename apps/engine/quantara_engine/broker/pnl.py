"""Canonical P&L conversion to account USD."""

from __future__ import annotations

from decimal import Decimal

from quantara_engine.broker.types import InstrumentSpec


def quote_pnl_to_usd(
    pnl_in_quote: Decimal,
    spec: InstrumentSpec,
    fx_rates: dict[str, Decimal],
) -> Decimal:
    """Convert realized/unrealized P&L from quote currency to account USD."""
    quote = spec.quote_currency.upper()
    if quote == "USD":
        return pnl_in_quote.quantize(Decimal("0.01"))
    rate = fx_rates.get(quote)
    if not rate or rate <= 0:
        raise ValueError(f"Missing FX rate for {quote}")
    return (pnl_in_quote / rate).quantize(Decimal("0.01"))


def unrealized_pnl_usd(
    qty: Decimal,
    avg: Decimal,
    mark: Decimal,
    spec: InstrumentSpec,
    fx_rates: dict[str, Decimal],
) -> Decimal:
    """Mark-to-market unrealized P&L in USD."""
    pnl_quote = (mark - avg) * qty
    return quote_pnl_to_usd(pnl_quote, spec, fx_rates)


def realized_pnl_usd(
    closed_qty: Decimal,
    avg: Decimal,
    fill_price: Decimal,
    is_long_close: bool,
    spec: InstrumentSpec,
    fx_rates: dict[str, Decimal],
) -> Decimal:
    """Realized P&L on a closed portion in USD."""
    if is_long_close:
        pnl_quote = (fill_price - avg) * closed_qty
    else:
        pnl_quote = (avg - fill_price) * closed_qty
    return quote_pnl_to_usd(pnl_quote, spec, fx_rates)
