"""Dataset fingerprint tests."""

from datetime import datetime, timezone
from decimal import Decimal

from quantara_engine.backtesting.fingerprint import compute_dataset_fingerprint
from quantara_engine.domain.types import Candle
from quantara_engine.market_data.adapters.mock import MockMarketDataProvider


def test_fingerprint_deterministic():
    provider = MockMarketDataProvider()
    candles = provider.generate_candles("xauusd", "1h", 50)
    fp1 = compute_dataset_fingerprint(candles)
    fp2 = compute_dataset_fingerprint(candles)
    assert fp1 == fp2
    assert len(fp1) == 64


def test_fingerprint_changes_when_ohlc_changes():
    provider = MockMarketDataProvider()
    candles = provider.generate_candles("xauusd", "1h", 10)
    fp1 = compute_dataset_fingerprint(candles)

    modified = list(candles)
    c = modified[5]
    modified[5] = Candle(
        instrument_id=c.instrument_id,
        timeframe=c.timeframe,
        timestamp=c.timestamp,
        open=c.open,
        high=c.high,
        low=c.low,
        close=c.close + Decimal("1.00"),
        volume=c.volume,
        source=c.source,
    )
    fp2 = compute_dataset_fingerprint(modified)
    assert fp1 != fp2


def test_fingerprint_order_independent():
    provider = MockMarketDataProvider()
    candles = provider.generate_candles("xauusd", "1h", 10)
    shuffled = list(reversed(candles))
    assert compute_dataset_fingerprint(candles) == compute_dataset_fingerprint(shuffled)
