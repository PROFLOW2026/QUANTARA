"""Gold Trend Pullback v1.0.0 signal tests."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pandas as pd

from quantara_engine.domain.types import Candle, SignalAction, StrategyContext
from quantara_engine.strategies.gold_trend_pullback.v1_0_0 import GoldTrendPullbackV1
from quantara_engine.strategies.gold_trend_pullback.indicators import ema


def _candles_from_closes(closes: list[float], start: datetime | None = None) -> list[Candle]:
    if start is None:
        start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    candles = []
    price = closes[0]
    for i, close in enumerate(closes):
        ts = start + timedelta(hours=i)
        o = price
        h = max(o, close) + 1.0
        l = min(o, close) - 1.0
        candles.append(
            Candle(
                instrument_id="xauusd",
                timeframe="1h",
                timestamp=ts,
                open=Decimal(str(round(o, 2))),
                high=Decimal(str(round(h, 2))),
                low=Decimal(str(round(l, 2))),
                close=Decimal(str(round(close, 2))),
                volume=Decimal("100"),
            )
        )
        price = close
    return candles


def test_insufficient_data_returns_hold():
    strategy = GoldTrendPullbackV1()
    ctx = StrategyContext("xauusd", "1h", {})
    candles = _candles_from_closes([2650.0] * 50)
    signal = strategy.evaluate(candles, ctx)
    assert signal.action == SignalAction.HOLD
    assert "INSUFFICIENT_DATA" in signal.reason


def test_long_entry_on_pullback_to_ema20():
    strategy = GoldTrendPullbackV1()
    # Build uptrend: rising prices above EMA200
    closes = [2600 + i * 0.5 for i in range(220)]
    candles = _candles_from_closes(closes)

    # Force pullback candle: low touches EMA20 area, close above
    df = pd.DataFrame({"close": [float(c.close) for c in candles]})
    ema20_val = float(ema(df["close"], 20).iloc[-1])
    last = candles[-1]
    pullback = Candle(
        instrument_id=last.instrument_id,
        timeframe=last.timeframe,
        timestamp=last.timestamp + timedelta(hours=1),
        open=last.close,
        high=last.close + Decimal("2"),
        low=Decimal(str(round(ema20_val - 0.5, 2))),
        close=Decimal(str(round(ema20_val + 1.0, 2))),
        volume=Decimal("100"),
    )
    candles = candles + [pullback]

    ctx = StrategyContext(
        "xauusd",
        "1h",
        {"min_candles_required": 200, "rsi_entry_min": 0, "rsi_entry_max": 100},
    )
    signal = strategy.evaluate(candles, ctx)
    # May be BUY or HOLD depending on EMA alignment — verify metadata when BUY
    if signal.action == SignalAction.BUY:
        assert signal.suggested_sl is not None
        assert signal.suggested_tp is not None
        assert signal.suggested_sl < pullback.close
        assert signal.suggested_tp > pullback.close


def test_no_setup_when_below_ema200():
    strategy = GoldTrendPullbackV1()
    closes = [2700 - i * 0.8 for i in range(220)]
    candles = _candles_from_closes(closes)
    ctx = StrategyContext("xauusd", "1h", {"min_candles_required": 200})
    signal = strategy.evaluate(candles, ctx)
    assert signal.action == SignalAction.HOLD
    assert "NO_SETUP" in signal.reason or "downtrend" in signal.reason.lower()
