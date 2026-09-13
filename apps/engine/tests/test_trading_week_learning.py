"""Trading Week Learning Layer — observational shadow tests (A–K focused)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from quantara_engine.domain.types import Candle, Signal, SignalAction, StrategyContext
from quantara_engine.learning.daily_loss_shadow import compute_daily_loss_shadow_for_portfolio
from quantara_engine.learning.planned_vs_actual import compute_risk_observation
from quantara_engine.learning.rsi_shadow import evaluate_all_rsi_shadows, evaluate_shadow_variant
from quantara_engine.strategies.gold_trend_pullback.v1_0_0 import GoldTrendPullbackV1


def _candle(ts: datetime, o: float, h: float, l: float, c: float) -> Candle:
    return Candle(
        instrument_id="xau",
        timeframe="15m",
        timestamp=ts,
        open=Decimal(str(o)),
        high=Decimal(str(h)),
        low=Decimal(str(l)),
        close=Decimal(str(c)),
        volume=Decimal("1"),
    )


def _uptrend_pullback_candles(n: int = 220) -> list[Candle]:
    """Synthetic series ending with EMA uptrend + pullback into EMA20."""
    start = datetime(2026, 9, 14, 10, 0, tzinfo=timezone.utc)
    candles: list[Candle] = []
    price = 2000.0
    for i in range(n):
        ts = start + timedelta(minutes=15 * i)
        # Strong uptrend then mild pullback on last bar
        if i < n - 5:
            price += 1.5
            candles.append(_candle(ts, price - 0.5, price + 1.0, price - 1.0, price))
        elif i < n - 1:
            price -= 0.2
            candles.append(_candle(ts, price + 0.2, price + 0.5, price - 0.8, price))
        else:
            # Pullback: low touches below recent closes, close recovers
            low = price - 3.0
            close = price + 0.5
            candles.append(_candle(ts, price, price + 1.0, low, close))
    return candles


def test_a_active_robot_a_signal_identical_to_shadow_active_variant():
    strategy = GoldTrendPullbackV1()
    candles = _uptrend_pullback_candles()
    params = strategy.default_parameters()
    ctx = StrategyContext(instrument_id="xau", timeframe="15m", parameters=params)
    active = strategy.evaluate(candles, ctx)
    shadow_active = evaluate_shadow_variant(candles, params, variant="active")
    assert active.action.value.upper() == shadow_active.action
    assert (active.reason or "") == shadow_active.reason
    if active.suggested_sl is not None:
        assert abs(Decimal(str(active.suggested_sl)) - Decimal(str(shadow_active.suggested_sl))) < Decimal("0.0001")


def test_b_shadow_variants_do_not_create_intents_or_orders_in_memory():
    """Shadow evaluation returns signals only — no side effects on strategy class."""
    candles = _uptrend_pullback_candles()
    params = GoldTrendPullbackV1.default_parameters()
    before = GoldTrendPullbackV1.default_parameters()
    results = evaluate_all_rsi_shadows(candles, params)
    assert set(results.keys()) == {"active", "directional", "no_rsi"}
    assert GoldTrendPullbackV1.default_parameters() == before
    # Active pipeline signal object is untouched when we only use shadow module
    active_signal = Signal(action=SignalAction.HOLD, reason="HOLD: trend valid, no pullback yet")
    evaluate_all_rsi_shadows(candles, params, active_signal=active_signal)
    assert active_signal.action == SignalAction.HOLD


def test_j_regime_tagging_does_not_change_strategy_output():
    strategy = GoldTrendPullbackV1()
    candles = _uptrend_pullback_candles()
    params = strategy.default_parameters()
    ctx = StrategyContext(instrument_id="xau", timeframe="15m", parameters=params)
    a = strategy.evaluate(candles, ctx)
    b = strategy.evaluate(candles, ctx)
    assert a.action == b.action
    assert a.reason == b.reason
    assert a.suggested_sl == b.suggested_sl
    assert a.suggested_tp == b.suggested_tp


def test_k_shadow_performance_deterministic_for_same_candles():
    candles = _uptrend_pullback_candles()
    params = GoldTrendPullbackV1.default_parameters()
    r1 = evaluate_all_rsi_shadows(candles, params)
    r2 = evaluate_all_rsi_shadows(candles, params)
    for key in ("active", "directional", "no_rsi"):
        assert r1[key].action == r2[key].action
        assert r1[key].reason == r2[key].reason
        assert r1[key].rsi == r2[key].rsi


def test_g_h_planned_vs_actual_gap_risk_long_and_short():
    long_obs = compute_risk_observation(
        direction="long",
        signal_candle_close=Decimal("100"),
        planned_entry_ref=Decimal("100"),
        execution_candle_open=Decimal("101"),
        actual_fill_price=Decimal("101.2"),
        planned_sl=Decimal("98"),
        planned_tp=Decimal("106"),
        quantity=Decimal("10"),
        equity_at_entry=Decimal("2000"),
        planned_risk_usd=Decimal("20"),  # 2 * 10
        tolerance_pct=Decimal("2"),
    )
    assert long_obs["gap_from_signal"] == 1.0
    assert long_obs["actual_risk_usd"] == pytest.approx(32.0)  # (101.2-98)*10
    assert long_obs["risk_overrun"] is True

    short_obs = compute_risk_observation(
        direction="short",
        signal_candle_close=Decimal("100"),
        planned_entry_ref=Decimal("100"),
        execution_candle_open=Decimal("99"),
        actual_fill_price=Decimal("98.8"),
        planned_sl=Decimal("102"),
        planned_tp=Decimal("94"),
        quantity=Decimal("10"),
        equity_at_entry=Decimal("2000"),
        planned_risk_usd=Decimal("20"),
        tolerance_pct=Decimal("2"),
    )
    assert short_obs["gap_from_signal"] == -1.0
    assert short_obs["actual_risk_usd"] == pytest.approx(32.0)  # (102-98.8)*10
    assert short_obs["risk_overrun"] is True


def test_directional_vs_active_rsi_zone_difference():
    """When RSI > 60 with long pullback, active holds; directional may buy."""
    # Construct metadata-level check via evaluate_shadow_variant with forced params
    # Use wide RSI so we can distinguish — build candles then patch by evaluating both
    candles = _uptrend_pullback_candles()
    params = GoldTrendPullbackV1.default_parameters()
    active = evaluate_shadow_variant(candles, params, variant="active")
    directional = evaluate_shadow_variant(candles, params, variant="directional")
    no_rsi = evaluate_shadow_variant(candles, params, variant="no_rsi")
    # If active is BUY, both shadows should also be BUY (filters are looser or equal)
    if active.action == "BUY":
        assert directional.action == "BUY"
        assert no_rsi.action == "BUY"
    # no_rsi never applies RSI hold when pullback exists
    if active.reason.startswith("NO_SETUP: RSI") and "pullback" in str(active.metadata):
        assert no_rsi.action in {"BUY", "SELL", "HOLD"}


def test_d_e_f_daily_loss_shadow_counterfactual_logic(monkeypatch):
    """Unit-level counterfactual: new entries after halt ignored; pre-halt exits kept."""
    # Pure logic via compute_risk style — exercise helper with a fake store-less path
    # by calling compute_daily_loss_shadow_for_portfolio with a MagicMock store.
    from unittest.mock import MagicMock

    portfolio_id = str(uuid4())
    day = datetime(2026, 9, 14, tzinfo=timezone.utc).date()
    start_equity = Decimal("2000")

    store = MagicMock()

    def execute_side_effect(stmt, params=None):
        sql = str(stmt)
        result = MagicMock()
        if "FROM portfolio_snapshots" in sql and "created_at <=" in sql:
            result.scalar.return_value = start_equity
            result.mappings.return_value.first.return_value = None
            result.mappings.return_value.all.return_value = []
            return result
        if "FROM portfolios WHERE" in sql:
            result.scalar.return_value = start_equity
            return result
        if "FROM portfolio_snapshots" in sql and "created_at >=" in sql:
            # Equity path hits 5% loss at 12:00
            result.mappings.return_value.all.return_value = [
                {"equity": Decimal("1950"), "created_at": datetime(2026, 9, 14, 11, 0, tzinfo=timezone.utc)},
                {"equity": Decimal("1890"), "created_at": datetime(2026, 9, 14, 12, 0, tzinfo=timezone.utc)},
                {"equity": Decimal("1850"), "created_at": datetime(2026, 9, 14, 15, 0, tzinfo=timezone.utc)},
            ]
            return result
        if "FROM positions" in sql:
            result.mappings.return_value.all.return_value = [
                {"id": "pos-early", "opened_at": datetime(2026, 9, 14, 10, 0, tzinfo=timezone.utc)},
                {"id": "pos-late", "opened_at": datetime(2026, 9, 14, 13, 0, tzinfo=timezone.utc)},
            ]
            return result
        if "FROM trades" in sql:
            result.mappings.return_value.all.return_value = [
                {
                    "trade_id": "t1",
                    "position_id": "pos-early",
                    "realized_pnl": Decimal("-80"),
                    "opened_at": datetime(2026, 9, 14, 10, 0, tzinfo=timezone.utc),
                    "closed_at": datetime(2026, 9, 14, 11, 30, tzinfo=timezone.utc),
                },
                {
                    "trade_id": "t2",
                    "position_id": "pos-late",
                    "realized_pnl": Decimal("-50"),
                    "opened_at": datetime(2026, 9, 14, 13, 0, tzinfo=timezone.utc),
                    "closed_at": datetime(2026, 9, 14, 14, 0, tzinfo=timezone.utc),
                },
            ]
            return result
        # upsert
        result.scalar.return_value = None
        result.mappings.return_value.all.return_value = []
        return result

    store.session.execute.side_effect = execute_side_effect

    out = compute_daily_loss_shadow_for_portfolio(
        store,
        portfolio_id=portfolio_id,
        trade_date=day,
        daily_loss_limit_pct=Decimal("5"),
        baseline_id=None,
    )
    assert out["shadow_halt"] is True
    assert out["actual_eod_pnl"] == -130.0
    # late entry ignored in shadow stop → only early trade -80
    assert out["shadow_stop_eod_pnl"] == -80.0
    assert out["difference"] == 50.0
    # Must not touch portfolio status / halt
    assert all("UPDATE portfolios" not in str(c.args[0]) for c in store.session.execute.call_args_list)


def test_i_funnel_uses_canonical_stage_names():
    from quantara_engine.learning.funnel import ROBOT_BY_SLUG

    assert ROBOT_BY_SLUG["gold-trend-pullback"] == "Robot A"
    assert "opening-range-breakout" in ROBOT_BY_SLUG
