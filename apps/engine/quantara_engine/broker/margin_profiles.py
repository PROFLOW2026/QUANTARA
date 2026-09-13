"""Broker-specific margin profiles — overrides global execution_product rules."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import text

from quantara_engine.broker.execution_product import EXECUTION_PRODUCT_RULES, ExecutionProduct
from quantara_engine.broker.types import AssetClassRules
from quantara_engine.broker.vendor import BrokerVendor
from quantara_engine.persistence.store import TradingStore


@dataclass(frozen=True)
class BrokerMarginProfile:
    slug: str
    broker_vendor: BrokerVendor
    execution_product: ExecutionProduct
    instrument_symbol: str | None
    rules: AssetClassRules
    locate_required: bool
    simulation_assumption: bool


def _rules_from_row(row: dict) -> AssetClassRules:
    return AssetClassRules(
        initial_margin_pct=Decimal(str(row["initial_margin_pct"])),
        maintenance_margin_pct=Decimal(str(row["maintenance_margin_pct"])),
        max_leverage=Decimal(str(row["max_leverage"])),
        max_order_notional=Decimal(str(row["max_order_notional"] or 0)),
        max_position_notional=Decimal(str(row["max_position_notional"] or 0)),
        shorting_allowed=bool(row["shorting_allowed"]),
        fractional_allowed=bool(row["fractional_allowed"]),
    )


def load_margin_profile(
    store: TradingStore,
    *,
    broker_vendor: BrokerVendor | str,
    execution_product: ExecutionProduct,
    instrument_symbol: str | None = None,
) -> BrokerMarginProfile:
    vendor = broker_vendor.value if isinstance(broker_vendor, BrokerVendor) else str(broker_vendor).upper()
    sym = instrument_symbol.upper().replace("/", "") if instrument_symbol else None
    row = None
    try:
        if sym:
            row = store.session.execute(
                text(
                    """
                    SELECT slug, broker_vendor::text, execution_product::text, instrument_symbol,
                           initial_margin_pct, maintenance_margin_pct, max_leverage,
                           max_order_notional, max_position_notional, shorting_allowed,
                           fractional_allowed, locate_required, simulation_assumption
                    FROM broker_margin_profiles
                    WHERE broker_vendor = CAST(:vendor AS broker_vendor)
                      AND execution_product = CAST(:product AS execution_product)
                      AND instrument_symbol = :sym
                    LIMIT 1
                    """
                ),
                {"vendor": vendor, "product": execution_product.value, "sym": sym},
            ).mappings().first()
        if not row:
            row = store.session.execute(
                text(
                    """
                    SELECT slug, broker_vendor::text, execution_product::text, instrument_symbol,
                           initial_margin_pct, maintenance_margin_pct, max_leverage,
                           max_order_notional, max_position_notional, shorting_allowed,
                           fractional_allowed, locate_required, simulation_assumption
                    FROM broker_margin_profiles
                    WHERE broker_vendor = CAST(:vendor AS broker_vendor)
                      AND execution_product = CAST(:product AS execution_product)
                      AND instrument_symbol IS NULL
                    LIMIT 1
                    """
                ),
                {"vendor": vendor, "product": execution_product.value},
            ).mappings().first()
    except Exception:
        store.session.rollback()
        row = None

    if row:
        return BrokerMarginProfile(
            slug=str(row["slug"]),
            broker_vendor=BrokerVendor(str(row["broker_vendor"])),
            execution_product=ExecutionProduct(str(row["execution_product"])),
            instrument_symbol=row.get("instrument_symbol"),
            rules=_rules_from_row(dict(row)),
            locate_required=bool(row.get("locate_required")),
            simulation_assumption=bool(row.get("simulation_assumption", True)),
        )

    fallback = EXECUTION_PRODUCT_RULES.get(execution_product)
    if not fallback:
        raise KeyError(f"No margin profile for {vendor}/{execution_product}")
    return BrokerMarginProfile(
        slug=f"fallback-{execution_product.value}",
        broker_vendor=BrokerVendor(vendor),
        execution_product=execution_product,
        instrument_symbol=sym,
        rules=fallback,
        locate_required=execution_product.value == "equity_margin_short",
        simulation_assumption=True,
    )
