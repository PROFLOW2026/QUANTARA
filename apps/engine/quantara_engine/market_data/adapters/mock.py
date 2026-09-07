"""Deterministic mock XAU/USD candle generator."""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP

from quantara_engine.domain.types import Candle
from quantara_engine.market_data.provider import MarketDataProvider


def _timeframe_minutes(timeframe: str) -> int:
    mapping = {"5m": 5, "15m": 15, "1h": 60}
    return mapping.get(timeframe, 60)


class MockMarketDataProvider:
    """Deterministic OHLCV generator seeded by instrument/timeframe/start."""

    def __init__(self, base_price: Decimal = Decimal("2650.00")) -> None:
        self.base_price = base_price
        self.source = "mock"

    def _seed(self, instrument_id: str, timeframe: str, index: int) -> float:
        key = f"{instrument_id}:{timeframe}:{index}".encode()
        digest = hashlib.sha256(key).hexdigest()
        return int(digest[:8], 16) / 0xFFFFFFFF

    def _build_candle(
        self,
        instrument_id: str,
        timeframe: str,
        timestamp: datetime,
        index: int,
    ) -> Candle:
        r1 = self._seed(instrument_id, timeframe, index)
        r2 = self._seed(instrument_id, timeframe, index + 1000)
        r3 = self._seed(instrument_id, timeframe, index + 2000)
        r4 = self._seed(instrument_id, timeframe, index + 3000)

        trend = Decimal(str((index % 400 - 200) * 0.05))
        open_ = (self.base_price + trend).quantize(Decimal("0.01"), ROUND_HALF_UP)
        volatility = Decimal(str(2 + r1 * 8))
        close = (open_ + Decimal(str((r2 - 0.5) * 2)) * volatility).quantize(
            Decimal("0.01"), ROUND_HALF_UP
        )
        high = max(open_, close) + Decimal(str(r3 * 5)).quantize(Decimal("0.01"))
        low = min(open_, close) - Decimal(str(r4 * 5)).quantize(Decimal("0.01"))
        volume = Decimal(str(100 + r1 * 500)).quantize(Decimal("0.01"))

        return Candle(
            instrument_id=instrument_id,
            timeframe=timeframe,
            timestamp=timestamp,
            open=open_,
            high=high,
            low=low,
            close=close,
            volume=volume,
            source=self.source,
            is_complete=True,
        )

    def generate_candles(
        self,
        instrument_id: str,
        timeframe: str,
        count: int,
        start: datetime | None = None,
    ) -> list[Candle]:
        minutes = _timeframe_minutes(timeframe)
        if start is None:
            start = datetime(2024, 1, 1, tzinfo=timezone.utc)
        candles: list[Candle] = []
        for i in range(count):
            ts = start + timedelta(minutes=minutes * i)
            candles.append(self._build_candle(instrument_id, timeframe, ts, i))
        return candles

    def fetch_latest(
        self,
        instrument_id: str,
        timeframe: str,
        since: datetime | None = None,
    ) -> list[Candle]:
        count = 1 if since is None else 5
        start = since or datetime.now(timezone.utc) - timedelta(hours=1)
        return self.generate_candles(instrument_id, timeframe, count, start)

    def fetch_range(
        self,
        instrument_id: str,
        timeframe: str,
        start: datetime,
        end: datetime,
    ) -> list[Candle]:
        minutes = _timeframe_minutes(timeframe)
        total_minutes = int((end - start).total_seconds() // 60)
        count = max(1, total_minutes // minutes + 1)
        candles = self.generate_candles(instrument_id, timeframe, count, start)
        return [c for c in candles if start <= c.timestamp <= end]
