"""Quote-currency → account-currency conversion for multi-currency P&L."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from typing import TYPE_CHECKING, Iterable

from quantara_engine.domain.types import Instrument, Position

if TYPE_CHECKING:
    from quantara_engine.persistence.store import TradingStore

logger = logging.getLogger(__name__)

ACCOUNT_CURRENCY = "USD"
_MONEY = Decimal("0.01")

# Conversion-only instrument — not part of the 8-asset competition universe.
USDJPY_DB_SYMBOL = "USDJPY"
USDJPY_PROVIDER_SYMBOL = "USD/JPY"


@dataclass(frozen=True)
class FxRateTable:
    """Quote currency units per 1 USD (e.g. JPY=150 → 1 USD = 150 JPY)."""

    quote_per_usd: dict[str, Decimal] = field(default_factory=dict)

    @classmethod
    def usd_only(cls) -> FxRateTable:
        return cls({ACCOUNT_CURRENCY: Decimal("1")})

    @classmethod
    def with_jpy(cls, jpy_per_usd: Decimal) -> FxRateTable:
        return cls({ACCOUNT_CURRENCY: Decimal("1"), "JPY": jpy_per_usd})

    def quote_to_account(self, amount_quote: Decimal, quote_currency: str) -> Decimal:
        quote = normalize_quote_currency(quote_currency)
        if quote == ACCOUNT_CURRENCY:
            return amount_quote.quantize(_MONEY)
        rate = self.quote_per_usd.get(quote)
        if rate is None or rate <= 0:
            raise ValueError(f"No FX rate for quote currency {quote!r}")
        return (amount_quote / rate).quantize(_MONEY)

    def quote_notional_to_account(
        self,
        quantity: Decimal,
        price: Decimal,
        quote_currency: str,
    ) -> Decimal:
        return self.quote_to_account(quantity * price, quote_currency)


def normalize_quote_currency(quote_currency: object) -> str:
    if isinstance(quote_currency, str) and quote_currency.strip():
        return quote_currency.upper()
    return ACCOUNT_CURRENCY


@dataclass
class CurrencyContext:
    """Instrument metadata + FX rates for canonical account-currency P&L."""

    instruments_by_id: dict[str, Instrument]
    fx_rates: FxRateTable

    @classmethod
    def usd_only(cls, instruments: dict[str, Instrument] | None = None) -> CurrencyContext:
        return cls(instruments or {}, FxRateTable.usd_only())

    def instrument_for(self, position: Position) -> Instrument:
        inst = self.instruments_by_id.get(position.instrument_id)
        if inst is None:
            return Instrument(
                id=position.instrument_id,
                symbol="UNKNOWN",
                name="Unknown",
                quote_currency=ACCOUNT_CURRENCY,
            )
        raw_quote = getattr(inst, "quote_currency", ACCOUNT_CURRENCY)
        if isinstance(raw_quote, str) and raw_quote.strip():
            return inst
        quote = normalize_quote_currency(raw_quote)
        return Instrument(
            id=inst.id,
            symbol=inst.symbol,
            name=inst.name,
            asset_class=inst.asset_class,
            base_currency=inst.base_currency,
            quote_currency=quote,
            pip_size=inst.pip_size,
            contract_size=inst.contract_size,
            price_tick_size=inst.price_tick_size,
            quantity_step=inst.quantity_step,
            min_quantity=inst.min_quantity,
            is_active=inst.is_active,
        )


def quote_currencies_for_instruments(instruments: Iterable[Instrument]) -> set[str]:
    quotes: set[str] = set()
    for inst in instruments:
        raw = getattr(inst, "quote_currency", None) or ACCOUNT_CURRENCY
        if isinstance(raw, str) and raw.strip():
            quotes.add(raw.upper())
        else:
            quotes.add(ACCOUNT_CURRENCY)
    return quotes


def resolve_fx_rates(store: TradingStore, quote_currencies: set[str]) -> FxRateTable:
    """Resolve live quote→USD rates from DB candles (USDJPY) with provider fallback."""
    rates: dict[str, Decimal] = {ACCOUNT_CURRENCY: Decimal("1")}
    needed = {c.upper() for c in quote_currencies if c.upper() != ACCOUNT_CURRENCY}
    if "JPY" in needed:
        rates["JPY"] = store.resolve_jpy_per_usd()
    for quote in needed - {"JPY"}:
        logger.warning("Unsupported quote currency %s — assuming 1:1 with USD", quote)
        rates[quote] = Decimal("1")
    return FxRateTable(quote_per_usd=rates)


def build_currency_context(
    store: TradingStore,
    instruments: Iterable[Instrument],
) -> CurrencyContext:
    inst_list = list(instruments)
    by_id = {i.id: i for i in inst_list}
    quotes = quote_currencies_for_instruments(inst_list)
    fx = resolve_fx_rates(store, quotes)
    return CurrencyContext(instruments_by_id=by_id, fx_rates=fx)


def usdjpy_instrument_row() -> dict:
    """Deterministic USDJPY conversion instrument metadata."""
    return {
        "id": uuid.UUID("00000000-0000-0000-0000-00000000usd1"),
        "symbol": USDJPY_DB_SYMBOL,
        "name": "USD/JPY",
        "asset_class": "forex",
        "base_currency": "USD",
        "quote_currency": "JPY",
    }
