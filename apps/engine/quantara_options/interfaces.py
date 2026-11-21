"""Internal service interfaces between QUANTARA Options modules."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Protocol, runtime_checkable

from quantara_options.contracts import (
    OptionChainResponse,
    OptionQuoteSnapshot,
)


@runtime_checkable
class OptionsDataProvider(Protocol):
    provider_name: str

    def get_underlying_price(self, symbol: str) -> tuple[Decimal | None, bool, list[str]]:
        """Return (spot_price, is_market_open, warnings)."""
        ...

    def get_option_chain(
        self,
        symbol: str,
        *,
        min_dte: int = 3,
        max_dte: int = 60,
        strike_range_pct: float = 0.20,
        as_of: datetime | None = None,
    ) -> OptionChainResponse:
        ...

    def get_quotes_for_occ(
        self,
        occ_symbols: list[str],
        *,
        underlying_hint: str | None = None,
    ) -> dict[str, OptionQuoteSnapshot]:
        ...

    def get_provider_health(self) -> dict:
        ...
