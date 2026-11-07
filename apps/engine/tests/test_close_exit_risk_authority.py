"""Exit authority: entry risk gates must not block CLOSE; shadow CLOSED needs broker fill."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock

from quantara_engine.broker.execution_service import BrokerExecutionResult
from quantara_engine.domain.types import (
    Candle,
    Direction,
    Instrument,
    Mode,
    Portfolio,
    PortfolioStatus,
    Position,
    PositionStatus,
    RiskProfile,
    Signal,
    SignalAction,
    StrategyInstance,
)
from quantara_engine.live_sim.close_authority import finalize_live_sim_position_close
from quantara_engine.live_sim.risk_policy import (
    LiveSimRiskSettings,
    OpenRiskSnapshot,
    evaluate_entry_gates,
)
from quantara_engine.risk.engine import RiskEngine, RiskEvaluationInput


def _settings() -> LiveSimRiskSettings:
    return LiveSimRiskSettings(
        risk_per_trade_pct=Decimal("1"),
        max_total_open_sl_risk_pct=Decimal("3"),
        max_symbol_sl_risk_pct=Decimal("1"),
        max_group_sl_risk_pct=Decimal("2"),
        daily_loss_gate_pct=Decimal("2"),
        max_drawdown_gate_pct=Decimal("10"),
        concentration_mode="ENFORCE",
        high_water_mark=Decimal("10000"),
        daily_start_equity=Decimal("10000"),
        daily_start_date="2026-10-29",
    )


def _portfolio(*, equity: str = "8500") -> Portfolio:
    return Portfolio(
        id="portfolio-1",
        name="test",
        mode=Mode.PAPER,
        initial_capital=Decimal("10000"),
        balance=Decimal(equity),
        equity=Decimal(equity),
        peak_equity=Decimal("10000"),
        status=PortfolioStatus.ACTIVE,
    )


def _profile() -> RiskProfile:
    return RiskProfile(
        id="rp1",
        slug="tight",
        name="Tight",
        risk_per_trade_pct=Decimal("1"),
        max_open_positions=1,
        max_total_exposure_pct=Decimal("10"),
        daily_loss_limit_pct=Decimal("2"),
        max_drawdown_pct=Decimal("5"),
    )


def _instance() -> StrategyInstance:
    return StrategyInstance(
        id="si-1",
        portfolio_id="portfolio-1",
        strategy_version_id="sv1",
        strategy_slug="gold-trend-pullback",
        strategy_version="1.0.0",
        instrument_id="inst-xau",
        timeframe="5m",
        risk_profile_id="rp1",
        parameter_overrides={},
        is_active=True,
    )


def _instrument() -> Instrument:
    return Instrument(
        id="inst-xau",
        symbol="XAUUSD",
        name="Gold",
        asset_class="fx",
        quantity_step=Decimal("0.01"),
        min_quantity=Decimal("0.01"),
    )


def _candle() -> Candle:
    return Candle(
        instrument_id="inst-xau",
        timeframe="5m",
        timestamp=datetime(2026, 10, 29, 14, 0, tzinfo=timezone.utc),
        open=Decimal("2650"),
        high=Decimal("2655"),
        low=Decimal("2645"),
        close=Decimal("2650"),
        volume=Decimal("1"),
    )


def _open_position() -> Position:
    return Position(
        id="pos-1",
        portfolio_id="portfolio-1",
        strategy_instance_id="si-1",
        instrument_id="inst-xau",
        direction=Direction.LONG,
        quantity=Decimal("1"),
        entry_price=Decimal("2700"),
        current_price=Decimal("2650"),
        stop_loss=Decimal("2600"),
        take_profit=Decimal("2800"),
        status=PositionStatus.OPEN,
        opened_at=datetime(2026, 10, 29, 12, 0, tzinfo=timezone.utc),
    )


def test_close_not_blocked_by_entry_only_risk_limits():
    """Daily-loss / drawdown / exposure gates block new entries, not CLOSE exits."""
    settings = _settings()
    open_risk = OpenRiskSnapshot(total_sl_risk_usd=Decimal("0"), by_symbol={}, by_group={})
    entry_gate = evaluate_entry_gates(
        settings=settings,
        equity=Decimal("8500"),
        realized_pnl_today=Decimal("-1500"),
        open_risk=open_risk,
        proposed_risk_usd=Decimal("50"),
        symbol="XAUUSD",
        as_of=datetime(2026, 10, 29, 14, 0, tzinfo=timezone.utc),
    )
    assert entry_gate.allowed is False
    assert entry_gate.reason in {"DAILY_LOSS_GATE", "DRAWDOWN_GATE"}

    # Same stressed account still has an open position — CLOSE must be approved.
    # Exposure would also deny a new BUY (qty * mark >> max_total_exposure_pct).
    engine = RiskEngine()
    open_pos = _open_position()
    buy_decision = engine.evaluate(
        RiskEvaluationInput(
            signal=Signal(
                action=SignalAction.BUY,
                reason="new entry while stressed",
                suggested_sl=Decimal("2600"),
                suggested_tp=Decimal("2800"),
            ),
            strategy_instance=_instance(),
            portfolio=_portfolio(equity="8500"),
            open_positions=[],
            risk_profile=_profile(),
            current_candle=_candle(),
            instrument=_instrument(),
        )
    )
    assert buy_decision.approved is False
    assert buy_decision.denial_reason is not None
    assert "MAX_DRAWDOWN" in buy_decision.denial_reason

    close_decision = engine.evaluate(
        RiskEvaluationInput(
            signal=Signal(action=SignalAction.CLOSE, reason="exit existing"),
            strategy_instance=_instance(),
            portfolio=_portfolio(equity="8500"),
            open_positions=[open_pos],
            risk_profile=_profile(),
            current_candle=_candle(),
            instrument=_instrument(),
        )
    )
    assert close_decision.approved is True
    assert close_decision.intent is not None
    assert close_decision.intent.is_close is True
    assert close_decision.intent.position_id == open_pos.id
    assert "close_signal" in close_decision.checks_passed


def test_live_sim_not_closed_before_physical_broker_confirmation():
    """Shadow live_sim_positions must stay OPEN until a physical broker close is confirmed."""
    store = MagicMock()
    store.session = MagicMock()

    rejected = BrokerExecutionResult(
        accepted=False,
        shadow_only=False,
        physical_closed_qty=Decimal("0"),
        fill_quantity=Decimal("0"),
        broker_order_id=None,
        broker_fill_id=None,
    )
    ok = finalize_live_sim_position_close(
        store,
        position_id="pos-shadow-1",
        closed_at=datetime(2026, 10, 29, 15, 0, tzinfo=timezone.utc),
        account_slug="live-sim-ibkr-like",
        instrument_symbol="XAUUSD",
        mark_price=Decimal("2650"),
        broker_res=rejected,
        requested_quantity=Decimal("1"),
    )
    assert ok is False
    store.session.execute.assert_not_called()

    accepted_without_physical = BrokerExecutionResult(
        accepted=True,
        shadow_only=False,
        physical_closed_qty=Decimal("0"),
        fill_quantity=Decimal("0"),
        broker_order_id="ord-pending",
        broker_fill_id=None,
    )
    ok2 = finalize_live_sim_position_close(
        store,
        position_id="pos-shadow-1",
        closed_at=datetime(2026, 10, 29, 15, 1, tzinfo=timezone.utc),
        account_slug="live-sim-ibkr-like",
        instrument_symbol="XAUUSD",
        mark_price=Decimal("2650"),
        broker_res=accepted_without_physical,
        requested_quantity=Decimal("1"),
    )
    assert ok2 is False
    store.session.execute.assert_not_called()
