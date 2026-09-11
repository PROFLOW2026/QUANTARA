"""Core execution / stacking corrections — regression tests."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from quantara_engine.domain.types import (
    Candle,
    Direction,
    Instrument,
    Portfolio,
    PortfolioStatus,
    Position,
    PositionStatus,
    RiskProfile,
    Signal,
    SignalAction,
    StrategyInstance,
)
from quantara_engine.execution.cost_profile import (
    EXECUTION_COST_BY_SYMBOL,
    describe_execution_cost,
    execution_assumptions_for,
    get_execution_cost_profile,
)
from quantara_engine.execution.economics import compute_executable_economics, tp_economically_valid
from quantara_engine.execution.fill_calculator import calculate_fill_price
from quantara_engine.portfolio.currency import FxRateTable
from quantara_engine.competition.constants import ACTIVE_COMPETITION_PORTFOLIOS
from quantara_engine.risk.engine import RiskEngine, RiskEvaluationInput
from quantara_engine.risk.opportunity import (
    opportunity_idempotency_key,
    opportunity_key_from_signal,
    orb_opportunity_key,
)
from quantara_engine.risk.sizing import compute_position_size


def _gbpjpy() -> Instrument:
    return Instrument(
        id="gbp",
        symbol="GBPJPY",
        name="GBP/JPY",
        asset_class="forex",
        base_currency="GBP",
        quote_currency="JPY",
        pip_size=Decimal("0.01"),
        contract_size=Decimal("100000"),
        price_tick_size=Decimal("0.001"),
        quantity_step=Decimal("1000"),
        min_quantity=Decimal("1000"),
    )


def _btc() -> Instrument:
    return Instrument(
        id="btc",
        symbol="BTCUSD",
        name="BTC/USD",
        asset_class="crypto",
        quote_currency="USD",
    )


def _portfolio(equity: str = "2000", *, competition: bool = False) -> Portfolio:
    pid = (
        ACTIVE_COMPETITION_PORTFOLIOS[0].portfolio_id
        if competition
        else "p1"
    )
    return Portfolio(
        id=pid,
        name="Test",
        mode="paper",
        initial_capital=Decimal(equity),
        balance=Decimal(equity),
        equity=Decimal(equity),
        status=PortfolioStatus.ACTIVE,
        peak_equity=Decimal(equity),
    )


def _profile(pct: str) -> RiskProfile:
    return RiskProfile(
        id="rp",
        slug="balanced",
        name="Balanced",
        risk_per_trade_pct=Decimal(pct),
        max_open_positions=10,
        max_total_exposure_pct=Decimal("500"),
        daily_loss_limit_pct=Decimal("10"),
        max_drawdown_pct=Decimal("50"),
    )


def _instance(slug: str = "gold-trend-pullback", tf: str = "5m") -> StrategyInstance:
    return StrategyInstance(
        id="si1",
        portfolio_id="p1",
        strategy_version_id="sv1",
        strategy_slug=slug,
        strategy_version="1.0.0",
        instrument_id="gbp",
        timeframe=tf,
        risk_profile_id="rp",
    )


def _candle(close: str = "200.0") -> Candle:
    return Candle(
        instrument_id="gbp",
        timeframe="5m",
        timestamp=datetime(2026, 9, 11, 10, 0, tzinfo=timezone.utc),
        open=Decimal(close),
        high=Decimal(str(float(close) + 0.05)),
        low=Decimal(str(float(close) - 0.05)),
        close=Decimal(close),
    )


FX150 = FxRateTable.with_jpy(Decimal("150"))


class TestFxSpread:
    def test_gbpjpy_spread_and_slippage_stable_across_price(self):
        inst = _gbpjpy()
        low = execution_assumptions_for(inst, Decimal("180"))
        high = execution_assumptions_for(inst, Decimal("210"))
        assert low.spread == high.spread == Decimal("0.02")
        assert low.slippage_per_side == high.slippage_per_side == Decimal("0.002")

    def test_gbpjpy_pip_spread_is_two_pips(self):
        profile = get_execution_cost_profile("GBPJPY")
        assert profile.spread_pips == Decimal("2.0")

    def test_long_entry_worse_than_base(self):
        inst = _gbpjpy()
        assumptions = execution_assumptions_for(inst, Decimal("200"))
        fill = calculate_fill_price(Direction.LONG, "entry", Decimal("200"), Decimal("1000"), assumptions)
        assert fill.fill_price > Decimal("200")

    def test_short_entry_better_than_base(self):
        inst = _gbpjpy()
        assumptions = execution_assumptions_for(inst, Decimal("200"))
        fill = calculate_fill_price(Direction.SHORT, "entry", Decimal("200"), Decimal("1000"), assumptions)
        assert fill.fill_price < Decimal("200")

    def test_round_trip_spread_cost_gbpjpy(self):
        desc = describe_execution_cost("GBPJPY", Decimal("200"))
        assert desc["spread_model"] == "pips"
        assert desc["round_trip_pips"] == 2.4  # 2 spread + 0.4 slippage (0.2/side)


class TestTpEconomics:
    def test_three_pip_example_full_breakdown(self):
        """GBPJPY uses pip-based slippage (0.2/side); 3-pip TP exceeds ~2.4 pip RT cost."""
        inst = _gbpjpy()
        entry_base = Decimal("200.000")
        tp_base = Decimal("200.030")  # 3 pips above entry
        qty = Decimal("1000")
        assumptions = execution_assumptions_for(inst, entry_base)

        entry_fill = calculate_fill_price(Direction.LONG, "entry", entry_base, qty, assumptions)
        tp_fill = calculate_fill_price(Direction.LONG, "exit", tp_base, qty, assumptions)
        ok, econ = tp_economically_valid(
            Direction.LONG, entry_base, tp_base, qty, inst, assumptions, FX150
        )

        assert assumptions.spread == Decimal("0.02")  # 2 pips full spread
        assert assumptions.slippage_per_side == Decimal("0.002")  # 0.2 pip per side
        assert entry_fill.spread_cost == Decimal("0.01")
        assert entry_fill.slippage == Decimal("0.002")
        assert tp_fill.slippage == Decimal("0.002")
        assert tp_base - entry_base == Decimal("0.03")
        assert econ.net_reward_usd > Decimal("0")
        assert ok

    @pytest.mark.parametrize(
        "tp_pips,expected_allowed",
        [
            (2.3, False),
            (2.4, False),
            (2.5, True),
        ],
    )
    def test_tp_boundary_uses_executable_net_not_pip_threshold(self, tp_pips, expected_allowed):
        inst = _gbpjpy()
        entry_base = Decimal("200.000")
        tp_base = entry_base + Decimal(str(tp_pips)) * Decimal("0.01")
        assumptions = execution_assumptions_for(inst, entry_base)
        ok, econ = tp_economically_valid(
            Direction.LONG,
            entry_base,
            tp_base,
            Decimal("1000"),
            inst,
            assumptions,
            FX150,
        )
        assert ok is expected_allowed
        if expected_allowed:
            assert econ.net_reward_usd > Decimal("0")
        else:
            assert econ.net_reward_usd <= Decimal("0")

    def test_tp_beyond_cost_allowed(self):
        inst = _gbpjpy()
        assumptions = execution_assumptions_for(inst, Decimal("200"))
        ok, econ = tp_economically_valid(
            Direction.LONG,
            Decimal("200"),
            Decimal("201.0"),
            Decimal("1000"),
            inst,
            assumptions,
            FX150,
        )
        assert ok
        assert econ.net_reward_usd > 0


class TestRiskSizing:
    @pytest.mark.parametrize(
        "pct,expected_target,expect_deny",
        [
            ("0.25", Decimal("5.00"), True),
            ("0.5", Decimal("10.00"), True),
            ("1", Decimal("20.00"), False),
            ("1.5", Decimal("30.00"), False),
            ("2", Decimal("40.00"), False),
        ],
    )
    def test_risk_tiers_target_amount(self, pct, expected_target, expect_deny):
        inst = _gbpjpy()
        assumptions = execution_assumptions_for(inst, Decimal("200"))
        qty, target, actual, deny = compute_position_size(
            _portfolio(),
            _profile(pct),
            inst,
            Decimal("200"),
            Decimal("198"),
            "long",
            [],
            Decimal("200"),
            allow_virtual_leverage=True,
            fx_rates=FX150,
            execution_assumptions=assumptions,
        )
        assert target == expected_target
        if expect_deny:
            assert deny == "MIN_QUANTITY_EXCEEDS_RISK_BUDGET"
        else:
            assert deny is None
            assert actual > 0
            assert actual <= target * Decimal("1.02")

    def test_executable_sl_includes_spread(self):
        inst = _gbpjpy()
        assumptions = execution_assumptions_for(inst, Decimal("200"))
        _, _, with_exec, _ = compute_position_size(
            _portfolio(),
            _profile("1"),
            inst,
            Decimal("200"),
            Decimal("198"),
            "long",
            [],
            Decimal("200"),
            allow_virtual_leverage=True,
            fx_rates=FX150,
            execution_assumptions=assumptions,
        )
        _, _, without_exec, _ = compute_position_size(
            _portfolio(),
            _profile("1"),
            inst,
            Decimal("200"),
            Decimal("198"),
            "long",
            [],
            Decimal("200"),
            allow_virtual_leverage=True,
            fx_rates=FX150,
            execution_assumptions=None,
        )
        assert with_exec >= without_exec


class TestOrbOpportunity:
    def test_opportunity_key_stable(self):
        k1 = orb_opportunity_key(
            symbol="GBPJPY",
            session_date="2026-09-11",
            direction="short",
            range_high=209.5,
            range_low=208.1,
        )
        k2 = orb_opportunity_key(
            symbol="GBPJPY",
            session_date="2026-09-11",
            direction="short",
            range_high=209.5,
            range_low=208.1,
        )
        assert k1 == k2

    def test_opposite_direction_different_key(self):
        long_k = orb_opportunity_key(
            symbol="GBPJPY",
            session_date="2026-09-11",
            direction="long",
            range_high=209.5,
            range_low=208.1,
        )
        short_k = orb_opportunity_key(
            symbol="GBPJPY",
            session_date="2026-09-11",
            direction="short",
            range_high=209.5,
            range_low=208.1,
        )
        assert long_k != short_k

    def test_idempotency_same_across_scheduler_runs(self):
        opp = orb_opportunity_key(
            symbol="GBPJPY",
            session_date="2026-09-11",
            direction="short",
            range_high=209.5,
            range_low=208.1,
        )
        k1 = opportunity_idempotency_key("instance-a", opp)
        k2 = opportunity_idempotency_key("instance-a", opp)
        assert k1 == k2


class TestRiskEngineStacking:
    def _entry_signal(self) -> Signal:
        return Signal(
            action=SignalAction.BUY,
            reason="test",
            suggested_sl=Decimal("198"),
            suggested_tp=Decimal("204"),
            metadata={
                "session_date": "2026-09-11",
                "opening_range_high": 201.0,
                "opening_range_low": 199.0,
            },
        )

    def test_same_opportunity_key_denied_while_open(self):
        engine = RiskEngine()
        open_pos = Position(
            id="pos1",
            portfolio_id="p1",
            strategy_instance_id="si1",
            instrument_id="gbp",
            direction=Direction.LONG,
            quantity=Decimal("1000"),
            entry_price=Decimal("200"),
            stop_loss=Decimal("198"),
            take_profit=Decimal("204"),
            current_price=Decimal("200"),
            status=PositionStatus.OPEN,
        )
        store = MagicMock()
        store.opportunity_consumed.return_value = True
        decision = engine.evaluate(
            RiskEvaluationInput(
                signal=self._entry_signal(),
                strategy_instance=_instance("opening-range-breakout"),
                portfolio=_portfolio(),
                open_positions=[open_pos],
                risk_profile=_profile("1"),
                current_candle=_candle(),
                instrument=_gbpjpy(),
                fx_rates=FX150,
                store=store,
            )
        )
        assert not decision.approved
        assert decision.denial_reason == "OPPORTUNITY_ALREADY_USED"

    def test_different_opportunity_allowed_while_prior_open(self):
        engine = RiskEngine()
        open_pos = Position(
            id="pos1",
            portfolio_id="p1",
            strategy_instance_id="si1",
            instrument_id="gbp",
            direction=Direction.LONG,
            quantity=Decimal("1000"),
            entry_price=Decimal("200"),
            stop_loss=Decimal("198"),
            take_profit=Decimal("204"),
            current_price=Decimal("200"),
            status=PositionStatus.OPEN,
        )
        store = MagicMock()
        store.opportunity_consumed.return_value = False
        store._position_to_domain = MagicMock(side_effect=lambda r: r)
        store._hydrate_position_strategy_versions = MagicMock()
        store.session.scalars.return_value.all.return_value = []
        store.session.scalar.return_value = Decimal("320000")
        new_pullback_candle = Candle(
            instrument_id="gbp",
            timeframe="5m",
            timestamp=datetime(2026, 9, 11, 11, 0, tzinfo=timezone.utc),
            open=Decimal("200"),
            high=Decimal("200.1"),
            low=Decimal("199.9"),
            close=Decimal("200"),
        )
        decision = engine.evaluate(
            RiskEvaluationInput(
                signal=Signal(
                    action=SignalAction.BUY,
                    reason="new pullback",
                    suggested_sl=Decimal("198"),
                    suggested_tp=Decimal("204"),
                ),
                strategy_instance=_instance("gold-trend-pullback"),
                portfolio=_portfolio(competition=True),
                open_positions=[open_pos],
                risk_profile=_profile("1"),
                current_candle=new_pullback_candle,
                instrument=_gbpjpy(),
                fx_rates=FX150,
                store=store,
            )
        )
        assert decision.approved, decision.denial_reason
        assert decision.metadata["opportunity_key"] == (
            "pullback:GBPJPY:5m:long:2026-09-11T11:00:00+00:00"
        )

    def test_opportunity_consumed_denied(self):
        engine = RiskEngine()
        store = MagicMock()
        store.opportunity_consumed.return_value = True
        decision = engine.evaluate(
            RiskEvaluationInput(
                signal=self._entry_signal(),
                strategy_instance=_instance("opening-range-breakout"),
                portfolio=_portfolio(),
                open_positions=[],
                risk_profile=_profile("1"),
                current_candle=_candle(),
                instrument=_gbpjpy(),
                fx_rates=FX150,
                store=store,
            )
        )
        assert not decision.approved
        assert decision.denial_reason == "OPPORTUNITY_ALREADY_USED"

    def test_tp_inside_cost_denied(self):
        engine = RiskEngine()
        store = MagicMock()
        store.opportunity_consumed.return_value = False
        signal = Signal(
            action=SignalAction.BUY,
            reason="test",
            suggested_sl=Decimal("198"),
            suggested_tp=Decimal("200.02"),
        )
        decision = engine.evaluate(
            RiskEvaluationInput(
                signal=signal,
                strategy_instance=_instance(),
                portfolio=_portfolio(),
                open_positions=[],
                risk_profile=_profile("1"),
                current_candle=_candle(),
                instrument=_gbpjpy(),
                fx_rates=FX150,
                store=store,
            )
        )
        assert not decision.approved
        assert decision.denial_reason == "TP_INSIDE_EXECUTION_COST"

    def test_different_timeframe_instances_allowed(self):
        engine = RiskEngine()
        store = MagicMock()
        store.opportunity_consumed.return_value = False
        store._position_to_domain = MagicMock(side_effect=lambda r: r)
        store._hydrate_position_strategy_versions = MagicMock()
        store.session.scalars.return_value.all.return_value = []
        store.session.scalar.return_value = Decimal("320000")
        open_pos = Position(
            id="pos1",
            portfolio_id="p1",
            strategy_instance_id="si-other",
            instrument_id="gbp",
            direction=Direction.LONG,
            quantity=Decimal("1000"),
            entry_price=Decimal("200"),
            stop_loss=Decimal("198"),
            take_profit=Decimal("204"),
            current_price=Decimal("200"),
            status=PositionStatus.OPEN,
        )
        decision = engine.evaluate(
            RiskEvaluationInput(
                signal=Signal(
                    action=SignalAction.BUY,
                    reason="test",
                    suggested_sl=Decimal("198"),
                    suggested_tp=Decimal("204"),
                ),
                strategy_instance=_instance(tf="15m"),
                portfolio=_portfolio(competition=True),
                open_positions=[open_pos],
                risk_profile=_profile("1"),
                current_candle=_candle(),
                instrument=_gbpjpy(),
                fx_rates=FX150,
                store=store,
            )
        )
        assert decision.approved, decision.denial_reason


class TestExecutionCostReport:
    def test_asset_cost_profiles_exist(self):
        for sym in ("BTCUSD", "ETHUSD", "XAUUSD", "NVDA", "TSLA", "AMD", "COIN"):
            assert sym in EXECUTION_COST_BY_SYMBOL
