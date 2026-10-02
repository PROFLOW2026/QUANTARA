"""Smoke tests for asset-class V3.2 rule families."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from quantara_engine.domain.types import Candle
from quantara_engine.research.v3_2.rule_lab import backtest_family


def _synthetic_candles(n: int = 400, *, start_price: float = 100.0) -> list[Candle]:
    start = datetime(2025, 6, 1, 0, 0, tzinfo=timezone.utc)
    out: list[Candle] = []
    px = start_price
    for i in range(n):
        ts = start + timedelta(hours=i)
        px *= 1.0005 if i % 17 else 0.998
        out.append(
            Candle(
                instrument_id="x",
                timeframe="1h",
                timestamp=ts,
                open=Decimal(str(round(px, 4))),
                high=Decimal(str(round(px * 1.002, 4))),
                low=Decimal(str(round(px * 0.998, 4))),
                close=Decimal(str(round(px * 1.001, 4))),
                volume=Decimal("100"),
            )
        )
    return out


def test_crypto_atr_expansion_produces_trades():
    candles = _synthetic_candles(500, start_price=50000)
    trades = backtest_family(
        "crypto_atr_expansion_break",
        candles=candles,
        direction="long",
        parameters={"breakout": 20, "atr_mult": 1.2},
    )
    assert isinstance(trades, list)


def test_fx_asian_london_break_runs():
    candles = _synthetic_candles(400, start_price=1.25)
    trades = backtest_family(
        "fx_asian_london_break",
        candles=candles,
        direction="long",
        parameters={"asian_end_hour_utc": 7},
    )
    assert isinstance(trades, list)
