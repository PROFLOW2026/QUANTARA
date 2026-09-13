"""Execution Product Router — maps direction + instrument to simulated broker product."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum

from quantara_engine.broker.execution_model import ExecutionModelVersion, uses_realistic_broker
from quantara_engine.broker.types import AssetClassRules
from quantara_engine.domain.types import Direction


class ExecutionProduct(str, Enum):
    CRYPTO_SPOT = "crypto_spot"
    CRYPTO_DERIVATIVE = "crypto_derivative"
    MARGIN_FX = "margin_fx"
    MARGIN_GOLD = "margin_gold"
    EQUITY_CASH = "equity_cash"
    EQUITY_MARGIN_SHORT = "equity_margin_short"


# Conservative simulation defaults — configurable per product, not broker-specific.
EXECUTION_PRODUCT_RULES: dict[ExecutionProduct, AssetClassRules] = {
    ExecutionProduct.CRYPTO_SPOT: AssetClassRules(
        initial_margin_pct=Decimal("100"),
        maintenance_margin_pct=Decimal("100"),
        max_leverage=Decimal("1"),
        max_order_notional=Decimal("50000"),
        max_position_notional=Decimal("100000"),
        shorting_allowed=False,
        fractional_allowed=True,
    ),
    ExecutionProduct.CRYPTO_DERIVATIVE: AssetClassRules(
        initial_margin_pct=Decimal("10"),  # 10x max simulated leverage
        maintenance_margin_pct=Decimal("5"),
        max_leverage=Decimal("10"),
        max_order_notional=Decimal("100000"),
        max_position_notional=Decimal("200000"),
        shorting_allowed=True,
        fractional_allowed=True,
    ),
    ExecutionProduct.MARGIN_FX: AssetClassRules(
        initial_margin_pct=Decimal("5"),
        maintenance_margin_pct=Decimal("2.5"),
        max_leverage=Decimal("20"),
        max_order_notional=Decimal("500000"),
        max_position_notional=Decimal("1000000"),
        shorting_allowed=True,
        fractional_allowed=False,
    ),
    ExecutionProduct.MARGIN_GOLD: AssetClassRules(
        initial_margin_pct=Decimal("10"),
        maintenance_margin_pct=Decimal("5"),
        max_leverage=Decimal("10"),
        max_order_notional=Decimal("200000"),
        max_position_notional=Decimal("400000"),
        shorting_allowed=True,
        fractional_allowed=True,
    ),
    ExecutionProduct.EQUITY_CASH: AssetClassRules(
        initial_margin_pct=Decimal("50"),
        maintenance_margin_pct=Decimal("25"),
        max_leverage=Decimal("2"),
        max_order_notional=Decimal("100000"),
        max_position_notional=Decimal("150000"),
        shorting_allowed=False,
        fractional_allowed=False,
    ),
    ExecutionProduct.EQUITY_MARGIN_SHORT: AssetClassRules(
        initial_margin_pct=Decimal("50"),
        maintenance_margin_pct=Decimal("25"),
        max_leverage=Decimal("2"),
        max_order_notional=Decimal("100000"),
        max_position_notional=Decimal("150000"),
        shorting_allowed=True,
        fractional_allowed=False,
    ),
}

# Default simulation cost assumptions (documented, configurable later via settings).
EXECUTION_SIM_DEFAULTS = {
    "commission_rate": Decimal("0.0004"),
    "latency_ms_market": 150,
    "partial_fill_threshold_qty": Decimal("0.35"),  # deterministic partial above this fraction
    "crypto_derivative_funding_rate_8h": Decimal("0.0001"),
    "equity_borrow_fee_annual_pct": Decimal("0.03"),
    "fx_overnight_financing_bps": Decimal("0.5"),
}


@dataclass(frozen=True)
class ExecutionProductRoute:
    product: ExecutionProduct
    asset_class_key: str
    short_capable: bool
    rules: AssetClassRules


def _normalize_direction(direction: str | Direction) -> str:
    if isinstance(direction, Direction):
        return direction.value
    return str(direction).lower()


def route_execution_product(
    symbol: str,
    direction: str | Direction,
    *,
    execution_model: ExecutionModelVersion,
    is_close: bool = False,
) -> ExecutionProductRoute:
    """Select execution product without strategy knowing broker/product details."""
    sym = symbol.upper().replace("/", "")
    dir_norm = _normalize_direction(direction)

    if is_close:
        # Closes use the product already held — router returns permissive route.
        return _route_open(sym, dir_norm, execution_model)

    return _route_open(sym, dir_norm, execution_model)


def _route_open(sym: str, dir_norm: str, execution_model: ExecutionModelVersion) -> ExecutionProductRoute:
    is_short = dir_norm == "short"
    realistic = uses_realistic_broker(execution_model)

    if sym in ("BTCUSD", "ETHUSD"):
        if realistic:
            product = ExecutionProduct.CRYPTO_DERIVATIVE
        elif is_short:
            product = ExecutionProduct.CRYPTO_SPOT  # legacy: blocked by shorting_allowed=False
        else:
            product = ExecutionProduct.CRYPTO_SPOT
        rules = EXECUTION_PRODUCT_RULES[product]
        return ExecutionProductRoute(
            product=product,
            asset_class_key="crypto_derivative" if product == ExecutionProduct.CRYPTO_DERIVATIVE else "crypto",
            short_capable=rules.shorting_allowed,
            rules=rules,
        )

    if sym == "GBPJPY":
        product = ExecutionProduct.MARGIN_FX
    elif sym == "XAUUSD":
        product = ExecutionProduct.MARGIN_GOLD
    elif sym in ("NVDA", "TSLA", "AMD", "COIN"):
        product = ExecutionProduct.EQUITY_MARGIN_SHORT if is_short else ExecutionProduct.EQUITY_CASH
    else:
        product = ExecutionProduct.EQUITY_CASH

    rules = EXECUTION_PRODUCT_RULES[product]
    return ExecutionProductRoute(
        product=product,
        asset_class_key=product.value.replace("_", " ").split()[0],
        short_capable=rules.shorting_allowed,
        rules=rules,
    )


def product_rules(product: ExecutionProduct) -> AssetClassRules:
    return EXECUTION_PRODUCT_RULES[product]
