"""Broker-specific fee engine — supports minimum commission and maker/taker."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Any

from sqlalchemy import text

from quantara_engine.broker.execution_product import ExecutionProduct
from quantara_engine.broker.vendor import BrokerVendor
from quantara_engine.persistence.store import TradingStore


class FeeSide(str, Enum):
    MAKER = "maker"
    TAKER = "taker"
    DEFAULT = "default"


@dataclass(frozen=True)
class FeeModel:
    fee_kind: str
    taker_rate: Decimal = Decimal("0")
    maker_rate: Decimal = Decimal("0")
    per_share_rate: Decimal = Decimal("0")
    per_contract_rate: Decimal = Decimal("0")
    bps_rate: Decimal = Decimal("0")
    minimum_per_order: Decimal = Decimal("0")
    maximum_per_order: Decimal | None = None
    default_side: FeeSide = FeeSide.TAKER
    simulation_assumption: bool = True

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> FeeModel:
        side = str(raw.get("default_side") or "taker").lower()
        return cls(
            fee_kind=str(raw.get("fee_kind") or "percentage_notional"),
            taker_rate=Decimal(str(raw.get("taker_rate") or 0)),
            maker_rate=Decimal(str(raw.get("maker_rate") or 0)),
            per_share_rate=Decimal(str(raw.get("per_share_rate") or 0)),
            per_contract_rate=Decimal(str(raw.get("per_contract_rate") or 0)),
            bps_rate=Decimal(str(raw.get("bps_rate") or 0)),
            minimum_per_order=Decimal(str(raw.get("minimum_per_order") or 0)),
            maximum_per_order=(
                Decimal(str(raw["maximum_per_order"])) if raw.get("maximum_per_order") else None
            ),
            default_side=FeeSide(side if side in ("maker", "taker") else "taker"),
            simulation_assumption=bool(raw.get("simulation_assumption", True)),
        )


@dataclass(frozen=True)
class CommissionResult:
    commission: Decimal
    fee_kind: str
    calculated_before_minimum: Decimal
    minimum_applied: bool
    side: FeeSide
    simulation_assumption: bool


def _apply_minimum(
    calculated: Decimal,
    minimum: Decimal,
    maximum: Decimal | None,
    *,
    fee_kind: str,
    side: FeeSide,
    simulation_assumption: bool,
) -> CommissionResult:
    commission = calculated
    minimum_applied = False
    if minimum > 0 and commission < minimum:
        commission = minimum
        minimum_applied = True
    if maximum is not None and commission > maximum:
        commission = maximum
    return CommissionResult(
        commission=commission.quantize(Decimal("0.0001")),
        fee_kind=fee_kind,
        calculated_before_minimum=calculated,
        minimum_applied=minimum_applied,
        side=side,
        simulation_assumption=simulation_assumption,
    )


def calculate_commission(
    *,
    fee_model: FeeModel,
    notional: Decimal,
    quantity: Decimal,
    side: FeeSide | None = None,
) -> CommissionResult:
    """Compute commission with max(calculated, minimum) support."""
    use_side = side or fee_model.default_side
    calculated = Decimal("0")

    if fee_model.fee_kind == "percentage_notional":
        rate = fee_model.maker_rate if use_side == FeeSide.MAKER else fee_model.taker_rate
        if rate <= 0 and use_side == FeeSide.MAKER:
            rate = fee_model.taker_rate
        calculated = (notional * rate).quantize(Decimal("0.0001"))
    elif fee_model.fee_kind == "per_share":
        calculated = (quantity * fee_model.per_share_rate).quantize(Decimal("0.0001"))
    elif fee_model.fee_kind == "per_contract":
        calculated = (quantity * fee_model.per_contract_rate).quantize(Decimal("0.0001"))
    elif fee_model.fee_kind == "bps_notional":
        calculated = (notional * fee_model.bps_rate / Decimal("10000")).quantize(Decimal("0.0001"))
    else:
        calculated = (notional * fee_model.taker_rate).quantize(Decimal("0.0001"))

    return _apply_minimum(
        calculated,
        fee_model.minimum_per_order,
        fee_model.maximum_per_order,
        fee_kind=fee_model.fee_kind,
        side=use_side,
        simulation_assumption=fee_model.simulation_assumption,
    )


def load_fee_profile(
    store: TradingStore,
    *,
    broker_vendor: BrokerVendor | str,
    execution_product: ExecutionProduct | str | None,
) -> FeeModel | None:
    vendor = broker_vendor.value if isinstance(broker_vendor, BrokerVendor) else str(broker_vendor).upper()
    product = execution_product.value if isinstance(execution_product, ExecutionProduct) else execution_product
    try:
        if product:
            row = store.session.execute(
                text(
                    """
                    SELECT fee_model, simulation_assumption
                    FROM broker_fee_profiles
                    WHERE broker_vendor = CAST(:vendor AS broker_vendor)
                      AND execution_product = CAST(:product AS execution_product)
                    ORDER BY created_at DESC
                    LIMIT 1
                    """
                ),
                {"vendor": vendor, "product": product},
            ).mappings().first()
        else:
            row = None
        if not row:
            row = store.session.execute(
                text(
                    """
                    SELECT fee_model, simulation_assumption
                    FROM broker_fee_profiles
                    WHERE broker_vendor = CAST(:vendor AS broker_vendor)
                      AND execution_product IS NULL
                    ORDER BY created_at DESC
                    LIMIT 1
                    """
                ),
                {"vendor": vendor},
            ).mappings().first()
    except Exception:
        store.session.rollback()
        return None
    if not row:
        return None
    model = FeeModel.from_json(dict(row["fee_model"] or {}))
    return FeeModel(
        fee_kind=model.fee_kind,
        taker_rate=model.taker_rate,
        maker_rate=model.maker_rate,
        per_share_rate=model.per_share_rate,
        per_contract_rate=model.per_contract_rate,
        bps_rate=model.bps_rate,
        minimum_per_order=model.minimum_per_order,
        maximum_per_order=model.maximum_per_order,
        default_side=model.default_side,
        simulation_assumption=bool(row.get("simulation_assumption", True)),
    )
