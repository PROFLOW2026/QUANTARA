"""End-to-end execution pipeline — intent fill, idempotency, live-sim retry, sizing."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from quantara_engine.domain.types import (
    Candle,
    Direction,
    Instrument,
    IntentStatus,
    OrderIntent,
    Portfolio,
    PortfolioStatus,
    RiskProfile,
    StrategyInstance,
    new_id,
)
from quantara_engine.execution.cost_profile import execution_assumptions_for
from quantara_engine.execution.live_intents import execute_pending_intents_live
from quantara_engine.execution.paper_broker import PaperBrokerAdapter
from quantara_engine.live_sim.allocator import maybe_allocate_live_sim
from quantara_engine.live_sim.candidate_log import allocation_lifecycle_state
from quantara_engine.live_sim.sizing import size_live_sim_entry
from quantara_engine.pipeline.candle_processor import CandleProcessor
from quantara_engine.portfolio.currency import FxRateTable
from quantara_engine.portfolio.service import PortfolioState
from quantara_engine.risk.sizing import select_quantity_for_risk_budget


def _btc() -> Instrument:
    return Instrument(
        id="btc-id",
        symbol="BTCUSD",
        name="BTC/USD",
        asset_class="crypto",
        quote_currency="USD",
        quantity_step=Decimal("0.0001"),
        min_quantity=Decimal("0.0001"),
    )


def test_btc_sizing_uses_risk_budget_not_min_qty():
    inst = _btc()
    entry = Decimal("77189.87")
    sl = Decimal("77547.21")
    assumptions = execution_assumptions_for(inst, entry)
    qty, risk, deny = select_quantity_for_risk_budget(
        target_risk=Decimal("100"),
        desired_quantity=Decimal("100") / abs(entry - sl),
        instrument=inst,
        direction=Direction.SHORT,
        entry_reference=entry,
        stop_loss=sl,
        fx_rates=FxRateTable.usd_only(),
        execution_assumptions=assumptions,
    )
    assert deny is None
    assert qty > Decimal("0.01")
    assert risk <= Decimal("102")
    assert risk >= Decimal("90")


def test_live_sim_sizing_respects_buying_power():
    inst = _btc()
    result = size_live_sim_entry(
        equity=Decimal("10000"),
        cash=Decimal("500"),
        target_risk=Decimal("100"),
        entry_reference=Decimal("50000"),
        stop_loss=Decimal("49000"),
        direction=Direction.LONG,
        instrument=inst,
        fx_rates=FxRateTable.usd_only(),
        execution_assumptions=execution_assumptions_for(inst, Decimal("50000")),
    )
    assert result.quantity <= Decimal("0.01")
    assert result.sizing_reason == "buying_power_cap"


def test_allocation_lifecycle_pending_vs_filled():
    pending = {
        "accepted": True,
        "metadata": {"pending_execution": True},
        "broker_order_id": None,
        "live_sim_position_id": None,
    }
    assert allocation_lifecycle_state(pending) == "pending_execution"
    filled = {**pending, "live_sim_position_id": "pos-1", "metadata": {"pending_execution": False}}
    assert allocation_lifecycle_state(filled) == "filled"


def test_execute_intents_fills_without_catchup_stale_guard():
    signal_ts = datetime(2026, 9, 12, 20, 50, tzinfo=timezone.utc)
    exec_ts = datetime(2026, 9, 12, 20, 55, tzinfo=timezone.utc)
    now = datetime(2026, 9, 12, 21, 0, tzinfo=timezone.utc)
    candle = Candle(
        instrument_id="btc-id",
        timeframe="5m",
        timestamp=exec_ts,
        open=Decimal("77000"),
        high=Decimal("77200"),
        low=Decimal("76900"),
        close=Decimal("77100"),
        volume=Decimal("1"),
    )
    portfolio = Portfolio(
        id="p1",
        name="ORB BTC",
        mode="paper",
        initial_capital=Decimal("2000"),
        balance=Decimal("2000"),
        equity=Decimal("2000"),
        status=PortfolioStatus.ACTIVE,
    )
    instance = StrategyInstance(
        id="si1",
        portfolio_id="p1",
        strategy_slug="opening-range-breakout",
        strategy_version="1.0.0",
        strategy_version_id="sv1",
        instrument_id="btc-id",
        timeframe="5m",
        risk_profile_id="rp1",
        parameter_overrides={},
    )
    intent = OrderIntent(
        id=new_id(),
        signal_id=new_id(),
        strategy_instance_id=instance.id,
        portfolio_id=portfolio.id,
        direction=Direction.LONG,
        quantity=Decimal("0.01"),
        stop_loss=Decimal("76000"),
        take_profit=Decimal("78000"),
        target_risk_amount=Decimal("20"),
        actual_risk_amount=Decimal("20"),
        signal_candle_timestamp=signal_ts,
        execution_candle_timestamp=exec_ts,
        risk_profile_id="rp1",
        status=IntentStatus.PENDING_EXECUTION,
    )
    instrument = _btc()
    risk = RiskProfile(
        id="rp1",
        slug="balanced",
        name="Balanced",
        risk_per_trade_pct=Decimal("1"),
        max_open_positions=5,
        max_total_exposure_pct=Decimal("500"),
        daily_loss_limit_pct=Decimal("10"),
        max_drawdown_pct=Decimal("50"),
    )
    store = MagicMock()
    store.list_all_competition_entries.return_value = (
        [],
        [],
        [{"portfolio": portfolio, "instance": instance, "risk_profile": risk}],
    )
    store.get_instrument_by_id.return_value = instrument
    store.list_recent_candles.return_value = [candle]
    store.list_pending_order_intents.return_value = [intent]
    store.load_portfolio_state.return_value = PortfolioState(portfolio=portfolio)
    store.build_currency_context_for_instruments.return_value = MagicMock(
        fx_rates=FxRateTable.usd_only()
    )
    store.get_settings_dict.return_value = {
        "trading_control_state": {"state": "running"},
        "paper_trading_enabled": True,
    }
    store.cancel_stale_pending_intents.return_value = 0
    store.session.commit = MagicMock()
    store.resolve_entry_opportunity_key.return_value = "opp:test"
    store.save_order = MagicMock()
    store.save_order_intent = MagicMock(side_effect=lambda i, **k: i)
    store.update_order_intent_status = MagicMock()
    store.sync_portfolios_financial_state_from_ledger = MagicMock()
    store.flush = MagicMock()

    with patch(
        "quantara_engine.broker.execution_bridge.execute_through_broker"
    ) as broker_exec:
        with patch(
            "quantara_engine.execution.live_intents.is_bar_complete",
            return_value=True,
        ):
            broker_exec.return_value = MagicMock(
                accepted=True,
                broker_order_id="ord-1",
                broker_fill_id="fill-1",
                physical_opened_qty=intent.quantity,
                from_existing_fill=False,
                shadow_only=False,
                decision=None,
            )
            report = execute_pending_intents_live(store, now)

    assert report["fills_attempted"] >= 1
    assert report["errors"] == []


def test_candle_processor_skips_risk_approved_for_terminal_intent():
    signal_ts = datetime(2026, 9, 12, 18, 0, tzinfo=timezone.utc)
    candle = Candle(
        instrument_id="btc-id",
        timeframe="5m",
        timestamp=signal_ts,
        open=Decimal("2500"),
        high=Decimal("2510"),
        low=Decimal("2490"),
        close=Decimal("2505"),
        volume=Decimal("1"),
    )
    portfolio = Portfolio(
        id="p1",
        name="Test",
        mode="paper",
        initial_capital=Decimal("2000"),
        balance=Decimal("2000"),
        equity=Decimal("2000"),
        status=PortfolioStatus.ACTIVE,
    )
    instance = StrategyInstance(
        id="si1",
        portfolio_id="p1",
        strategy_slug="ema-crossover",
        strategy_version="1.0.0",
        strategy_version_id="sv1",
        instrument_id="btc-id",
        timeframe="5m",
        risk_profile_id="rp1",
        parameter_overrides={},
    )
    store = MagicMock()
    store.get_settings_dict.return_value = {
        "trading_control_state": {"state": "running"},
        "paper_trading_enabled": True,
    }
    store.opportunity_consumed.return_value = True
    store.has_duplicate_entry_for_signal.return_value = False
    store.build_currency_context_for_instruments.return_value = MagicMock(
        fx_rates=FxRateTable.usd_only()
    )

    proc = CandleProcessor(
        portfolio_state=PortfolioState(portfolio=portfolio),
        strategy_instance=instance,
        instrument=_btc(),
        risk_profile=MagicMock(),
        broker=PaperBrokerAdapter(_btc().id, execution_assumptions_for(_btc(), candle.close)),
        store=store,
        allow_live_execution=True,
        execution_now=signal_ts + timedelta(minutes=5),
    )
    proc.all_candles = [candle]
    from quantara_engine.domain.types import Signal, SignalAction

    signal = Signal(
        action=SignalAction.BUY,
        reason="test",
        suggested_sl=Decimal("2480"),
        suggested_tp=Decimal("2540"),
    )
    proc._handle_trade_signal(signal, candle, new_id(), proc.all_candles)
    assert not any(d.decision_type.value == "risk_approved" for d in proc.decisions)


def test_live_sim_pending_allocation_resumes_not_duplicates():
    store = MagicMock()
    account = {
        "id": "acc-1",
        "equity": "10000",
        "cash": "10000",
        "starting_cash": "10000",
        "is_active": True,
        "pending_owner_reset": False,
        "risk_settings": {"risk_per_trade_pct": 1.0},
    }
    existing = {
        "id": "log-1",
        "accepted": True,
        "broker_order_id": None,
        "live_sim_position_id": None,
        "metadata": {"pending_execution": True},
        "signal_candle_timestamp": datetime(2026, 9, 12, 18, 0, tzinfo=timezone.utc),
        "symbol": "BTCUSD",
        "timeframe": "5m",
        "direction": "long",
        "proposed_entry": Decimal("77000"),
        "stop_loss": Decimal("76500"),
        "take_profit": Decimal("78000"),
        "calculated_risk_usd": Decimal("50"),
        "calculated_quantity": Decimal("0.01"),
        "strategy_slug": "gold-trend-pullback",
        "strategy_version": "1.0.0",
        "robot_label": "Robot A",
        "opportunity_key": "opp-1",
        "canonical_opportunity_key": "canon-1",
        "created_at": datetime(2026, 9, 12, 18, 0, tzinfo=timezone.utc),
    }
    with patch("quantara_engine.live_sim.allocator._account_row", return_value=account):
        with patch(
            "quantara_engine.live_sim.allocator.find_allocation_by_canonical",
            return_value=existing,
        ):
            with patch(
                "quantara_engine.live_sim.allocator._resume_pending_allocation",
                return_value={"status": "queued", "log_id": "log-1"},
            ) as resume:
                from quantara_engine.domain.types import Signal, SignalAction

                signal = Signal(
                    action=SignalAction.BUY,
                    reason="test",
                    suggested_sl=Decimal("76500"),
                    suggested_tp=Decimal("78000"),
                )
                candle = Candle(
                    instrument_id="btc-id",
                    timeframe="5m",
                    timestamp=existing["signal_candle_timestamp"],
                    open=Decimal("77000"),
                    high=Decimal("77100"),
                    low=Decimal("76900"),
                    close=Decimal("77000"),
                    volume=Decimal("1"),
                )
                result = maybe_allocate_live_sim(
                    store,
                    entry={"instance": MagicMock(strategy_slug="gold-trend-pullback", timeframe="5m")},
                    instrument=_btc(),
                    candle=candle,
                    candles=[candle],
                    candle_index=0,
                    signal=signal,
                    execution_now=datetime(2026, 9, 12, 18, 5, tzinfo=timezone.utc),
                )
    resume.assert_called_once()
    assert result["status"] == "queued"
