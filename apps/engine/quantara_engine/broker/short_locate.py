"""Deterministic equity short locate/borrow simulation — not broker-specific."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum

from quantara_engine.broker.execution_product import EXECUTION_SIM_DEFAULTS, ExecutionProduct


class LocateStatus(str, Enum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    RESTRICTED = "restricted"


@dataclass(frozen=True)
class ShortLocateResult:
    status: LocateStatus
    borrow_fee_annual_pct: Decimal
    detail: str = ""


# Liquid US equities — normally available in simulation.
_DEFAULT_AVAILABLE = frozenset({"NVDA", "TSLA", "AMD", "COIN"})

# Deterministic restricted symbol for testing (hash-based, stable).
_RESTRICTED_TEST_SYMBOLS = frozenset({"RESTRICTED", "HARDTOBORROW"})


def _deterministic_unavailable(symbol: str, account_slug: str) -> bool:
    raw = f"{account_slug}:{symbol}:locate".encode("utf-8")
    digest = hashlib.sha256(raw).hexdigest()
    unit = int(digest[:8], 16) / int("ffffffff", 16)
    return unit < 0.02  # 2% deterministic unavailable rate for non-default symbols


def check_short_locate(
    symbol: str,
    *,
    account_slug: str,
    execution_product: ExecutionProduct | None = None,
) -> ShortLocateResult:
    """Pre-submission locate check for equity margin short."""
    sym = symbol.upper().replace("/", "")
    fee = EXECUTION_SIM_DEFAULTS.get("equity_borrow_fee_annual_pct", Decimal("0.03"))

    if execution_product is not None and execution_product != ExecutionProduct.EQUITY_MARGIN_SHORT:
        return ShortLocateResult(status=LocateStatus.AVAILABLE, borrow_fee_annual_pct=Decimal("0"))

    if sym in _RESTRICTED_TEST_SYMBOLS:
        return ShortLocateResult(
            status=LocateStatus.RESTRICTED,
            borrow_fee_annual_pct=fee,
            detail=f"short locate restricted for {sym}",
        )

    if sym in _DEFAULT_AVAILABLE:
        return ShortLocateResult(status=LocateStatus.AVAILABLE, borrow_fee_annual_pct=fee)

    if _deterministic_unavailable(sym, account_slug):
        return ShortLocateResult(
            status=LocateStatus.UNAVAILABLE,
            borrow_fee_annual_pct=fee,
            detail=f"no borrow locate available for {sym}",
        )

    return ShortLocateResult(status=LocateStatus.AVAILABLE, borrow_fee_annual_pct=fee)
