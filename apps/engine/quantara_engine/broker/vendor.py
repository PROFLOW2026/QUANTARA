"""First-class broker vendor and environment identity."""

from __future__ import annotations

from enum import Enum


class BrokerVendor(str, Enum):
    SIMULATED = "SIMULATED"
    IBKR = "IBKR"
    KRAKEN = "KRAKEN"


class BrokerEnvironment(str, Enum):
    SIMULATION = "SIMULATION"
    PAPER = "PAPER"
    LIVE = "LIVE"


class BrokerConnectionState(str, Enum):
    CONNECTED = "CONNECTED"
    DEGRADED = "DEGRADED"
    RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"
    HALTED = "HALTED"
    DISCONNECTED = "DISCONNECTED"


def vendor_label_he(vendor: BrokerVendor | str) -> str:
    key = vendor.value if isinstance(vendor, BrokerVendor) else str(vendor).upper()
    return {
        "SIMULATED": "סימולציה",
        "IBKR": "IBKR",
        "KRAKEN": "Kraken",
    }.get(key, key)


def parse_vendor(value: str | None) -> BrokerVendor:
    if not value:
        return BrokerVendor.SIMULATED
    return BrokerVendor(str(value).upper())
