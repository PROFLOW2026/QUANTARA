"""Canonical instrument → execution product / broker symbol mapping."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import text

from quantara_engine.broker.execution_product import ExecutionProduct
from quantara_engine.broker.instruments import get_instrument_spec
from quantara_engine.persistence.store import TradingStore


@dataclass(frozen=True)
class InstrumentExecutionMapping:
    canonical_symbol: str
    market_data_symbol: str | None
    broker_symbol: str | None
    broker_product_id: str | None
    execution_product: ExecutionProduct
    venue: str | None
    asset_class: str
    base_currency: str
    quote_currency: str
    contract_multiplier: Decimal
    tick_size: Decimal
    quantity_step: Decimal
    min_quantity: Decimal
    min_notional: Decimal


def _fallback_mapping(symbol: str) -> InstrumentExecutionMapping:
    spec = get_instrument_spec(symbol)
    product = ExecutionProduct.EQUITY_CASH
    sym = symbol.upper().replace("/", "")
    if sym in ("BTCUSD", "ETHUSD"):
        product = ExecutionProduct.CRYPTO_SPOT
    elif sym == "GBPJPY":
        product = ExecutionProduct.MARGIN_FX
    elif sym == "XAUUSD":
        product = ExecutionProduct.MARGIN_GOLD
    return InstrumentExecutionMapping(
        canonical_symbol=sym,
        market_data_symbol=None,
        broker_symbol=sym,
        broker_product_id=None,
        execution_product=product,
        venue=None,
        asset_class=spec.asset_class,
        base_currency=spec.base_currency,
        quote_currency=spec.quote_currency,
        contract_multiplier=spec.contract_size,
        tick_size=spec.tick_size,
        quantity_step=spec.quantity_step,
        min_quantity=spec.min_quantity,
        min_notional=spec.min_notional,
    )


def load_instrument_mapping(store: TradingStore, symbol: str) -> InstrumentExecutionMapping:
    sym = symbol.upper().replace("/", "")
    try:
        row = store.session.execute(
            text(
                """
                SELECT canonical_symbol, market_data_symbol, broker_symbol, broker_product_id,
                       execution_product::text, venue, asset_class, base_currency, quote_currency,
                       contract_multiplier, tick_size, quantity_step, min_quantity, min_notional
                FROM instrument_execution_mappings
                WHERE canonical_symbol = :sym
                LIMIT 1
                """
            ),
            {"sym": sym},
        ).mappings().first()
    except Exception:
        store.session.rollback()
        row = None
    if not row:
        return _fallback_mapping(sym)
    return InstrumentExecutionMapping(
        canonical_symbol=str(row["canonical_symbol"]),
        market_data_symbol=row.get("market_data_symbol"),
        broker_symbol=row.get("broker_symbol"),
        broker_product_id=row.get("broker_product_id"),
        execution_product=ExecutionProduct(str(row["execution_product"])),
        venue=row.get("venue"),
        asset_class=str(row["asset_class"]),
        base_currency=str(row["base_currency"]),
        quote_currency=str(row["quote_currency"]),
        contract_multiplier=Decimal(str(row["contract_multiplier"])),
        tick_size=Decimal(str(row["tick_size"])),
        quantity_step=Decimal(str(row["quantity_step"])),
        min_quantity=Decimal(str(row["min_quantity"])),
        min_notional=Decimal(str(row["min_notional"])),
    )
