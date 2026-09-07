"""Market data provider protocol."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from quantara_engine.domain.types import Candle


class MarketDataProvider(Protocol):
    def fetch_latest(
        self,
        instrument_id: str,
        timeframe: str,
        since: datetime | None = None,
    ) -> list[Candle]:
        ...

    def fetch_range(
        self,
        instrument_id: str,
        timeframe: str,
        start: datetime,
        end: datetime,
    ) -> list[Candle]:
        ...

    def generate_candles(
        self,
        instrument_id: str,
        timeframe: str,
        count: int,
        start: datetime | None = None,
    ) -> list[Candle]:
        ...
