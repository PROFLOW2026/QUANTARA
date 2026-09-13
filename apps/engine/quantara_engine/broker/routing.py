"""First-class broker routing — strategy → product → broker account → adapter."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import text

from quantara_engine.broker.execution_model import ExecutionModelVersion, uses_realistic_broker
from quantara_engine.broker.execution_product import (
    EXECUTION_PRODUCT_RULES,
    ExecutionProduct,
    ExecutionProductRoute,
    route_execution_product as legacy_route_execution_product,
)
from quantara_engine.broker.margin_profiles import load_margin_profile
from quantara_engine.broker.vendor import BrokerVendor
from quantara_engine.domain.types import Direction
from quantara_engine.persistence.store import TradingStore

LIVE_SIM_OWNER_SLUG = "live-sim-owner"
RESEARCH_OWNER_SLUG = "research-paper-owner"


@dataclass(frozen=True)
class BrokerRouteDecision:
    canonical_symbol: str
    direction: str
    execution_product: ExecutionProduct
    broker_vendor: BrokerVendor
    broker_account_id: str | None
    broker_account_slug: str | None
    rules: object
    route_source: str  # db_rule | legacy_fallback


def _normalize_direction(direction: str | Direction) -> str:
    if isinstance(direction, Direction):
        return direction.value
    return str(direction).lower()


def _resolve_product(
    symbol: str,
    direction: str,
    *,
    execution_model: ExecutionModelVersion,
    is_close: bool,
) -> ExecutionProduct:
    route = legacy_route_execution_product(
        symbol, direction, execution_model=execution_model, is_close=is_close
    )
    return route.product


def _lookup_db_routing_rule(
    store: TradingStore,
    *,
    owner_portfolio_id: str | None,
    symbol: str,
    direction: str,
) -> BrokerVendor | None:
    sym = symbol.upper().replace("/", "")
    dir_enum = direction.upper()
    for portfolio_scope in (owner_portfolio_id, None):
        try:
            row = store.session.execute(
                text(
                    """
                    SELECT broker_vendor::text
                    FROM broker_routing_rules
                    WHERE enabled = TRUE
                      AND canonical_symbol = :sym
                      AND (direction IS NULL OR direction::text = LOWER(:dir))
                      AND (
                        (:pid IS NULL AND owner_portfolio_id IS NULL)
                        OR owner_portfolio_id = CAST(:pid AS uuid)
                      )
                    ORDER BY
                      CASE WHEN owner_portfolio_id IS NOT NULL THEN 0 ELSE 1 END,
                      priority ASC
                    LIMIT 1
                    """
                ),
                {"sym": sym, "dir": dir_enum, "pid": portfolio_scope},
            ).scalar()
        except Exception:
            store.session.rollback()
            row = None
        if row:
            return BrokerVendor(str(row).upper())
    return None


def resolve_broker_account_for_vendor(
    store: TradingStore,
    *,
    owner_portfolio_id: str,
    broker_vendor: BrokerVendor,
) -> dict | None:
    try:
        return store.session.execute(
            text(
                """
                SELECT ba.id::text, ba.slug, ba.broker_vendor::text, ba.connection_state::text,
                       ba.reconciliation_halted, ba.cash, ba.equity, ba.available_margin,
                       pba.allocated_capital, pba.enabled, pba.is_legacy_primary
                FROM portfolio_broker_accounts pba
                JOIN broker_accounts ba ON ba.id = pba.broker_account_id
                WHERE pba.owner_portfolio_id = CAST(:pid AS uuid)
                  AND ba.broker_vendor = CAST(:vendor AS broker_vendor)
                  AND pba.enabled = TRUE
                ORDER BY pba.is_legacy_primary DESC, pba.created_at ASC
                LIMIT 1
                """
            ),
            {"pid": owner_portfolio_id, "vendor": broker_vendor.value},
        ).mappings().first()
    except Exception:
        store.session.rollback()
        return None


def route_to_broker(
    store: TradingStore,
    *,
    symbol: str,
    direction: str | Direction,
    execution_model: ExecutionModelVersion,
    owner_portfolio_slug: str | None = None,
    is_close: bool = False,
) -> BrokerRouteDecision:
    """Select execution product, broker vendor, and target account."""
    dir_norm = _normalize_direction(direction)
    sym = symbol.upper().replace("/", "")

    owner_portfolio_id = None
    multi_broker = False
    if owner_portfolio_slug:
        try:
            row = store.session.execute(
                text(
                    """
                    SELECT id::text, multi_broker_mode_enabled
                    FROM owner_trading_portfolios WHERE slug = :slug
                    """
                ),
                {"slug": owner_portfolio_slug},
            ).mappings().first()
        except Exception:
            store.session.rollback()
            row = None
        if row:
            owner_portfolio_id = row["id"]
            multi_broker = bool(row["multi_broker_mode_enabled"])

    product = _resolve_product(sym, dir_norm, execution_model=execution_model, is_close=is_close)
    vendor = _lookup_db_routing_rule(
        store,
        owner_portfolio_id=owner_portfolio_id,
        symbol=sym,
        direction=dir_norm,
    )
    route_source = "db_rule"
    if vendor is None:
        route_source = "legacy_fallback"
        if sym in ("BTCUSD", "ETHUSD") and uses_realistic_broker(execution_model):
            vendor = BrokerVendor.KRAKEN if dir_norm == "short" or product == ExecutionProduct.CRYPTO_DERIVATIVE else BrokerVendor.KRAKEN
            if product == ExecutionProduct.CRYPTO_SPOT and dir_norm == "long":
                vendor = BrokerVendor.KRAKEN
        elif sym in ("BTCUSD", "ETHUSD"):
            vendor = BrokerVendor.SIMULATED
        elif sym in ("NVDA", "TSLA", "AMD", "COIN", "GBPJPY", "XAUUSD"):
            vendor = BrokerVendor.IBKR
        else:
            vendor = BrokerVendor.SIMULATED

    broker_account_id = None
    broker_account_slug = None

    if owner_portfolio_id and multi_broker:
        acct = resolve_broker_account_for_vendor(
            store, owner_portfolio_id=owner_portfolio_id, broker_vendor=vendor
        )
        if acct:
            broker_account_id = acct["id"]
            broker_account_slug = acct["slug"]
    elif owner_portfolio_id and not multi_broker:
        try:
            legacy = store.session.execute(
                text(
                    """
                    SELECT ba.id::text, ba.slug
                    FROM portfolio_broker_accounts pba
                    JOIN broker_accounts ba ON ba.id = pba.broker_account_id
                    WHERE pba.owner_portfolio_id = CAST(:pid AS uuid)
                      AND pba.is_legacy_primary = TRUE
                      AND pba.enabled = TRUE
                    LIMIT 1
                    """
                ),
                {"pid": owner_portfolio_id},
            ).mappings().first()
        except Exception:
            store.session.rollback()
            legacy = None
        if legacy:
            broker_account_id = legacy["id"]
            broker_account_slug = legacy["slug"]
            vendor = BrokerVendor.SIMULATED

    margin = load_margin_profile(
        store,
        broker_vendor=vendor,
        execution_product=product,
        instrument_symbol=sym,
    )

    return BrokerRouteDecision(
        canonical_symbol=sym,
        direction=dir_norm,
        execution_product=product,
        broker_vendor=vendor,
        broker_account_id=broker_account_id,
        broker_account_slug=broker_account_slug,
        rules=margin.rules,
        route_source=route_source,
    )


def legacy_execution_product_route(
    symbol: str,
    direction: str | Direction,
    *,
    execution_model: ExecutionModelVersion,
    is_close: bool = False,
) -> ExecutionProductRoute:
    """Backward-compatible product routing for Research (unchanged behavior)."""
    return legacy_route_execution_product(symbol, direction, execution_model=execution_model, is_close=is_close)
