"""Canonical instrument → execution product / broker symbol mapping."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import text

from quantara_engine.broker.execution_product import ExecutionProduct
from quantara_engine.broker.instruments import get_instrument_spec
from quantara_engine.persistence.store import TradingStore

DEFAULT_LEGACY_VENDOR = "SIMULATED"


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


def _row_to_mapping(row: dict) -> InstrumentExecutionMapping:
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


def _fallback_mapping(symbol: str, *, execution_product: ExecutionProduct | None = None) -> InstrumentExecutionMapping:
    spec = get_instrument_spec(symbol)
    product = execution_product or ExecutionProduct.EQUITY_CASH
    sym = symbol.upper().replace("/", "")
    if execution_product is None:
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


def _normalize_product(value: ExecutionProduct | str | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, ExecutionProduct):
        return value.value
    return str(value)


def load_instrument_mapping(
    store: TradingStore,
    symbol: str,
    *,
    broker_vendor: str | None = None,
    broker_account_id: str | None = None,
    execution_product: ExecutionProduct | str | None = None,
) -> InstrumentExecutionMapping:
    """Resolve mapping scoped by vendor/account/product — never assumes one row per canonical."""
    sym = symbol.upper().replace("/", "")
    vendor_filter = (broker_vendor or DEFAULT_LEGACY_VENDOR).upper()
    product_filter = _normalize_product(execution_product)
    try:
        row = store.session.execute(
            text(
                """
                SELECT canonical_symbol, market_data_symbol, broker_symbol, broker_product_id,
                       execution_product::text, venue, asset_class, base_currency, quote_currency,
                       contract_multiplier, tick_size, quantity_step, min_quantity, min_notional
                FROM instrument_execution_mappings
                WHERE canonical_symbol = :sym
                  AND broker_vendor = CAST(:vendor AS broker_vendor)
                  AND (
                    :product IS NULL
                    OR execution_product::text = :product
                  )
                  AND (
                    CAST(:account_id AS text) IS NULL
                    OR broker_account_id = CAST(:account_id AS uuid)
                    OR broker_account_id IS NULL
                  )
                ORDER BY
                  CASE WHEN :product IS NOT NULL THEN 0 ELSE 1 END,
                  CASE WHEN broker_account_id IS NOT NULL THEN 0 ELSE 1 END,
                  execution_product::text
                LIMIT 1
                """
            ),
            {
                "sym": sym,
                "vendor": vendor_filter,
                "account_id": broker_account_id,
                "product": product_filter,
            },
        ).mappings().first()
    except Exception:
        store.session.rollback()
        row = None
    if not row:
        prod_enum = ExecutionProduct(product_filter) if product_filter else None
        return _fallback_mapping(sym, execution_product=prod_enum)
    return _row_to_mapping(dict(row))


def resolve_instrument_mapping_for_route(
    store: TradingStore,
    *,
    symbol: str,
    broker_vendor: str,
    execution_product: ExecutionProduct | str,
    broker_account_id: str | None = None,
) -> InstrumentExecutionMapping:
    """Strict resolver for routed execution — always passes execution_product."""
    return load_instrument_mapping(
        store,
        symbol,
        broker_vendor=broker_vendor,
        broker_account_id=broker_account_id,
        execution_product=execution_product,
    )


def load_legacy_simulated_mapping(
    store: TradingStore,
    symbol: str,
    *,
    execution_product: ExecutionProduct | str | None = None,
) -> InstrumentExecutionMapping:
    """Research + single-account Live Sim backward-compatible path."""
    return load_instrument_mapping(
        store,
        symbol,
        broker_vendor=DEFAULT_LEGACY_VENDOR,
        execution_product=execution_product,
    )


def load_all_mappings_for_symbol(store: TradingStore, symbol: str) -> list[InstrumentExecutionMapping]:
    sym = symbol.upper().replace("/", "")
    try:
        rows = store.session.execute(
            text(
                """
                SELECT canonical_symbol, market_data_symbol, broker_symbol, broker_product_id,
                       execution_product::text, venue, asset_class, base_currency, quote_currency,
                       contract_multiplier, tick_size, quantity_step, min_quantity, min_notional
                FROM instrument_execution_mappings
                WHERE canonical_symbol = :sym
                ORDER BY broker_vendor, execution_product
                """
            ),
            {"sym": sym},
        ).mappings().all()
    except Exception:
        store.session.rollback()
        return []
    return [_row_to_mapping(dict(r)) for r in rows]
