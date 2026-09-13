"""Deterministic leveraged-product liquidation model — simulation defaults only."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from quantara_engine.broker.types import AssetClassRules


# Minimum distance to liquidation (fraction of mark) before new risk-increasing entry denied.
DEFAULT_MIN_LIQUIDATION_DISTANCE_PCT = Decimal("3")


@dataclass(frozen=True)
class LiquidationState:
    liquidation_price: Decimal | None
    margin_ratio_pct: Decimal | None
    distance_to_liquidation_pct: Decimal | None
    in_margin_call: bool
    in_liquidation: bool


def liquidation_price(
    *,
    direction: str,
    average_price: Decimal,
    rules: AssetClassRules,
) -> Decimal | None:
    """Isolated-margin style liquidation threshold — conservative simulation."""
    if rules.initial_margin_pct >= Decimal("100"):
        return None
    buffer = (rules.initial_margin_pct - rules.maintenance_margin_pct) / Decimal("100")
    if buffer <= 0:
        return None
    if direction == "long" or (average_price > 0 and direction.lower() == "long"):
        return (average_price * (Decimal("1") - buffer)).quantize(Decimal("0.00000001"))
    return (average_price * (Decimal("1") + buffer)).quantize(Decimal("0.00000001"))


def distance_to_liquidation_pct(
    *,
    direction: str,
    mark_price: Decimal,
    liq_price: Decimal | None,
) -> Decimal | None:
    if liq_price is None or mark_price <= 0:
        return None
    dir_norm = direction.lower()
    if dir_norm == "long":
        dist = (mark_price - liq_price) / mark_price * Decimal("100")
    else:
        dist = (liq_price - mark_price) / mark_price * Decimal("100")
    return dist.quantize(Decimal("0.0001"))


def margin_ratio_pct(equity: Decimal, maintenance_required: Decimal) -> Decimal | None:
    if maintenance_required <= 0:
        return None
    return (equity / maintenance_required * Decimal("100")).quantize(Decimal("0.01"))


def evaluate_liquidation_state(
    *,
    direction: str,
    average_price: Decimal,
    mark_price: Decimal,
    rules: AssetClassRules,
    equity: Decimal,
    maintenance_required: Decimal,
    margin_call_level_pct: Decimal = Decimal("100"),
    liquidation_level_pct: Decimal = Decimal("50"),
) -> LiquidationState:
    liq = liquidation_price(direction=direction, average_price=average_price, rules=rules)
    dist = distance_to_liquidation_pct(direction=direction, mark_price=mark_price, liq_price=liq)
    ratio = margin_ratio_pct(equity, maintenance_required)
    in_call = ratio is not None and ratio <= margin_call_level_pct
    in_liq = ratio is not None and ratio <= liquidation_level_pct
    return LiquidationState(
        liquidation_price=liq,
        margin_ratio_pct=ratio,
        distance_to_liquidation_pct=dist,
        in_margin_call=in_call,
        in_liquidation=in_liq,
    )


def liquidation_proximity_denied(
    *,
    direction: str,
    average_price: Decimal,
    mark_price: Decimal,
    rules: AssetClassRules,
    min_distance_pct: Decimal = DEFAULT_MIN_LIQUIDATION_DISTANCE_PCT,
) -> tuple[bool, str]:
    """Deny risk-increasing entry when projected position would be too close to liquidation."""
    liq = liquidation_price(direction=direction, average_price=average_price, rules=rules)
    dist = distance_to_liquidation_pct(direction=direction, mark_price=mark_price, liq_price=liq)
    if dist is None:
        return False, ""
    buffer_pct = rules.initial_margin_pct - rules.maintenance_margin_pct
    adaptive_min = min(min_distance_pct, buffer_pct * Decimal("0.6"))
    if dist < adaptive_min:
        return True, f"liquidation proximity {dist}% < minimum {adaptive_min}%"
    return False, ""
