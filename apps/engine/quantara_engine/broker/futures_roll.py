"""Futures contract roll primitives — strategy sees canonical symbol, execution selects contract."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import text

from quantara_engine.broker.vendor import BrokerVendor
from quantara_engine.persistence.store import TradingStore


@dataclass(frozen=True)
class FuturesContractSpec:
    canonical_symbol: str
    broker_vendor: BrokerVendor
    executable_symbol: str
    contract_multiplier: Decimal
    tick_size: Decimal
    expiry_date: date | None
    contract_month: str | None
    roll_date: date | None
    next_contract_symbol: str | None
    product_type: str | None


@dataclass(frozen=True)
class PlannedRoll:
    canonical_symbol: str
    from_contract: str
    to_contract: str
    roll_date: date
    status: str


def load_active_futures_mapping(
    store: TradingStore,
    *,
    canonical_symbol: str,
    broker_vendor: BrokerVendor | str,
    as_of: date | None = None,
) -> FuturesContractSpec | None:
    vendor = broker_vendor.value if isinstance(broker_vendor, BrokerVendor) else str(broker_vendor).upper()
    sym = canonical_symbol.upper().replace("/", "")
    row = store.session.execute(
        text(
            """
            SELECT canonical_symbol, broker_vendor::text, broker_symbol, contract_multiplier,
                   tick_size, expiry_date, contract_month, roll_date, next_contract_symbol,
                   product_type
            FROM instrument_execution_mappings
            WHERE canonical_symbol = :sym
              AND broker_vendor = CAST(:vendor AS broker_vendor)
              AND product_type IN ('micro_futures', 'futures')
            ORDER BY
              CASE WHEN expiry_date IS NULL OR expiry_date >= CURRENT_DATE THEN 0 ELSE 1 END,
              expiry_date ASC NULLS LAST
            LIMIT 1
            """
        ),
        {"sym": sym, "vendor": vendor},
    ).mappings().first()
    if not row:
        return None
    return FuturesContractSpec(
        canonical_symbol=sym,
        broker_vendor=BrokerVendor(str(row["broker_vendor"])),
        executable_symbol=str(row["broker_symbol"]),
        contract_multiplier=Decimal(str(row["contract_multiplier"])),
        tick_size=Decimal(str(row["tick_size"])),
        expiry_date=row.get("expiry_date"),
        contract_month=row.get("contract_month"),
        roll_date=row.get("roll_date"),
        next_contract_symbol=row.get("next_contract_symbol"),
        product_type=row.get("product_type"),
    )


def plan_roll(
    store: TradingStore,
    *,
    canonical_symbol: str,
    broker_vendor: BrokerVendor,
    from_contract: str,
    to_contract: str,
    roll_date: date,
) -> PlannedRoll:
    store.session.execute(
        text(
            """
            INSERT INTO futures_contract_rolls (
              canonical_symbol, broker_vendor, from_contract_symbol,
              to_contract_symbol, roll_date, status
            ) VALUES (
              :sym, CAST(:vendor AS broker_vendor), :from_sym, :to_sym, :roll_date, 'planned'
            )
            """
        ),
        {
            "sym": canonical_symbol.upper(),
            "vendor": broker_vendor.value,
            "from_sym": from_contract,
            "to_sym": to_contract,
            "roll_date": roll_date,
        },
    )
    store.session.commit()
    return PlannedRoll(
        canonical_symbol=canonical_symbol.upper(),
        from_contract=from_contract,
        to_contract=to_contract,
        roll_date=roll_date,
        status="planned",
    )
