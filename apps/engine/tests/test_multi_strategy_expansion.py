"""Robots C/D/E, regime engine, and seed idempotency tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import MagicMock

import numpy as np
import pytest

from quantara_engine.competition.multi_strategy_constants import (
    ALL_MULTI_STRATEGY_PORTFOLIOS,
    MEAN_REVERSION_PORTFOLIOS,
    MOMENTUM_CONTINUATION_PORTFOLIOS,
    SHADOW_REFERENCE_TOTAL,
    VOLATILITY_SQUEEZE_PORTFOLIOS,
)
from quantara_engine.domain.types import Candle, SignalAction, StrategyContext
from quantara_engine.market_regime.classifier import (
    StructureRegime,
    VolatilityRegime,
    classify_regime,
)
from quantara_engine.strategies.mean_reversion.v1_0_0 import MeanReversionV1
from quantara_engine.strategies.momentum_continuation.v1_0_0 import MomentumContinuationV1
from quantara_engine.strategies.volatility_squeeze.v1_0_0 import VolatilitySqueezeV1


def _candle(i: int, price: float, *, spread: float = 1.0, timeframe: str = "15m", step_minutes: int = 15) -> Candle:
    ts = datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=step_minutes * i)
    p = Decimal(str(price))
    d = Decimal(str(spread / 2))
    return Candle(
        instrument_id="btc",
        timeframe=timeframe,
        timestamp=ts,
        open=p,
        high=p + d,
        low=p - d,
        close=p,
        volume=Decimal("1000"),
    )


def _trending_candles(count: int = 80, start: float = 100.0, step: float = 0.8) -> list[Candle]:
    return [_candle(i, start + i * step) for i in range(count)]


def _flat_noise_candles(count: int = 80, center: float = 100.0) -> list[Candle]:
    rng = np.random.default_rng(42)
    return [_candle(i, center + float(rng.normal(0, 0.2))) for i in range(count)]


def _ctx(**runtime) -> StrategyContext:
    return StrategyContext(
        instrument_id="btc",
        timeframe="15m",
        parameters={},
        runtime={"db_symbol": "BTCUSD", **runtime},
    )


class TestPortfolioConstants:
    def test_portfolio_counts(self):
        assert len(MEAN_REVERSION_PORTFOLIOS) == 40
        assert len(VOLATILITY_SQUEEZE_PORTFOLIOS) == 40
        assert len(MOMENTUM_CONTINUATION_PORTFOLIOS) == 40
        assert len(ALL_MULTI_STRATEGY_PORTFOLIOS) == 120

    def test_unique_portfolio_ids(self):
        ids = [p.portfolio_id for p in ALL_MULTI_STRATEGY_PORTFOLIOS]
        assert len(ids) == len(set(ids))

    def test_shadow_reference_total(self):
        assert SHADOW_REFERENCE_TOTAL == Decimal("560000")


class TestMeanReversionStrategy:
    def test_insufficient_data(self):
        signal = MeanReversionV1().evaluate(_trending_candles(10), _ctx())
        assert signal.action == SignalAction.HOLD
        assert signal.reason == "insufficient_data"

    def test_strong_trend_rejection(self):
        candles = _trending_candles(80, step=1.2)
        signal = MeanReversionV1().evaluate(candles, _ctx())
        assert signal.action == SignalAction.HOLD
        assert signal.reason in {"strong_trend_no_mean_reversion", "no_mean_reversion_setup"}


class TestVolatilitySqueezeStrategy:
    def test_no_compression_on_trend(self):
        candles = _trending_candles(130, step=0.5)
        signal = VolatilitySqueezeV1().evaluate(candles, _ctx())
        assert signal.action == SignalAction.HOLD
        assert signal.reason in {"no_compression_detected", "compression_no_breakout_yet", "insufficient_data"}


class TestMomentumStrategy:
    def test_rejects_without_hourly_confirmation(self):
        candles = _trending_candles(80, step=0.6)
        signal = MomentumContinuationV1().evaluate(candles, _ctx())
        assert signal.action == SignalAction.HOLD
        assert signal.reason in {"no_hourly_confirmation", "no_momentum_setup", "insufficient_directional_strength", "overextended_from_mean"}

    def test_rejects_overextended_with_confirmation(self):
        candles = _trending_candles(80, step=2.5)
        h1 = [
            _candle(i, 100 + i * 2, spread=2.0, timeframe="1h", step_minutes=60)
            for i in range(60)
        ]
        signal = MomentumContinuationV1().evaluate(
            candles,
            _ctx(confirmation_candles_1h=h1),
        )
        assert signal.action == SignalAction.HOLD


class TestRegimeClassifier:
    def test_trend_up_on_uptrend(self):
        snapshot = classify_regime(_trending_candles(80, step=1.0))
        assert snapshot.structure_regime in {StructureRegime.TREND_UP, StructureRegime.TRANSITION}

    def test_range_on_flat(self):
        snapshot = classify_regime(_flat_noise_candles(80))
        assert snapshot.structure_regime in {
            StructureRegime.RANGE,
            StructureRegime.TRANSITION,
            StructureRegime.UNKNOWN,
        }

    def test_no_lookahead_uses_last_candle_only(self):
        candles = _trending_candles(80)
        snap_a = classify_regime(candles[:60])
        snap_b = classify_regime(candles[:61])
        assert "close" in snap_a.metrics and "close" in snap_b.metrics
        assert snap_a.metrics["close"] != snap_b.metrics["close"]


class TestRiskConcentration:
    def test_empty_positions(self):
        from quantara_engine.risk.concentration import build_physical_concentration

        store = MagicMock()
        store.list_all_competition_entries.return_value = ([], [], [])
        store.session.execute.return_value.all.return_value = []
        payload = build_physical_concentration(store, broker_equity=Decimal("320000"))
        assert payload["mode"] == "OBSERVE"
        assert payload["symbols"] == []
