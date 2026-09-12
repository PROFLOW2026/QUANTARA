"""Broker capability pre-gate — account-specific profile rules."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from quantara_engine.broker.accounts import LIVE_SIM_10K_ACCOUNT_SLUG, RESEARCH_PAPER_ACCOUNT_SLUG
from quantara_engine.broker.capability import (
    check_entry_capability_for_account,
    check_entry_capability_for_portfolio,
    entry_direction_allowed,
)
from quantara_engine.broker.profile import QUANTARA_LIVE_SIM_10K, QUANTARA_STANDARD_PAPER
from quantara_engine.competition.constants import ACTIVE_COMPETITION_PORTFOLIOS
from quantara_engine.domain.types import (
    Candle,
    Direction,
    Instrument,
    OrderIntent,
    Portfolio,
    PortfolioStatus,
    RiskProfile,
    StrategyInstance,
    new_id,
)
from quantara_engine.execution.cost_profile import execution_assumptions_for
from quantara_engine.execution.paper_broker import PaperBrokerAdapter
from quantara_engine.live_sim.allocator import maybe_allocate_live_sim
from quantara_engine.pipeline.candle_processor import CandleProcessor
from quantara_engine.portfolio.currency import FxRateTable
from quantara_engine.portfolio.service import PortfolioState
from quantara_engine.risk.engine import RiskDecision


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


def _xau() -> Instrument:
    return Instrument(
        id="xau-id",
        symbol="XAUUSD",
        name="XAU/USD",
        asset_class="commodity",
        quote_currency="USD",
        quantity_step=Decimal("0.01"),
        min_quantity=Decimal("0.01"),
    )


def _gbpjpy() -> Instrument:
    return Instrument(
        id="gj-id",
        symbol="GBPJPY",
        name="GBP/JPY",
        asset_class="forex",
        quote_currency="JPY",
        quantity_step=Decimal("1000"),
        min_quantity=Decimal("1000"),
    )


@pytest.mark.parametrize(
    "symbol,asset_class,profile",
    [
        ("BTCUSD", "crypto", QUANTARA_STANDARD_PAPER),
        ("ETHUSD", "crypto", QUANTARA_LIVE_SIM_10K),
    ],
)
def test_crypto_short_blocked_by_profile(symbol, asset_class, profile):
    result = entry_direction_allowed(profile, asset_class, "short")
    assert not result.allowed
    assert result.reason == "short_not_allowed"


@pytest.mark.parametrize(
    "symbol,asset_class",
    [
        ("BTCUSD", "crypto"),
        ("ETHUSD", "crypto"),
    ],
)
def test_crypto_long_allowed(symbol, asset_class):
    result = entry_direction_allowed(QUANTARA_STANDARD_PAPER, asset_class, "long")
    assert result.allowed


@pytest.mark.parametrize(
    "instrument",
    [_xau(), _gbpjpy()],
)
def test_non_crypto_short_allowed_at_capability_layer(instrument):
    cap = check_entry_capability_for_account(
        RESEARCH_PAPER_ACCOUNT_SLUG,
        instrument,
        "short",
    )
    assert cap.allowed
    assert cap.account_slug == RESEARCH_PAPER_ACCOUNT_SLUG


def test_research_btc_short_blocked_before_intent():
    portfolio_id = ACTIVE_COMPETITION_PORTFOLIOS[0].portfolio_id
    signal_ts = datetime(2026, 9, 12, 18, 0, tzinfo=timezone.utc)
    candle = Candle(
        instrument_id="btc-id",
        timeframe="5m",
        timestamp=signal_ts,
        open=Decimal("77000"),
        high=Decimal("77100"),
        low=Decimal("76900"),
        close=Decimal("77050"),
        volume=Decimal("1"),
    )
    portfolio = Portfolio(
        id=portfolio_id,
        name="BTC Test",
        mode="paper",
        initial_capital=Decimal("2000"),
        balance=Decimal("2000"),
        equity=Decimal("2000"),
        status=PortfolioStatus.ACTIVE,
    )
    instance = StrategyInstance(
        id="si1",
        portfolio_id=portfolio_id,
        strategy_slug="gold-trend-pullback",
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
        portfolio_id=portfolio_id,
        direction=Direction.SHORT,
        quantity=Decimal("0.01"),
        stop_loss=Decimal("77500"),
        take_profit=Decimal("76500"),
        target_risk_amount=Decimal("5"),
        actual_risk_amount=Decimal("5"),
        signal_candle_timestamp=signal_ts,
        risk_profile_id="rp1",
    )
    store = MagicMock()
    store.get_settings_dict.return_value = {
        "trading_control_state": {"state": "running"},
        "paper_trading_enabled": True,
    }
    store.opportunity_consumed.return_value = False
    store.has_duplicate_entry_for_signal.return_value = False
    store.find_pending_intent_for_signal_candle.return_value = None
    store.build_currency_context_for_instruments.return_value = MagicMock(
        fx_rates=FxRateTable.usd_only()
    )
    store.save_order_intent = MagicMock()

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
    proc.all_candles = [candle, candle]
    proc.risk_engine = MagicMock()
    proc.risk_engine.evaluate.return_value = RiskDecision(approved=True, intent=intent)

    from quantara_engine.domain.types import Signal, SignalAction

    signal = Signal(
        action=SignalAction.SELL,
        reason="Rally to EMA20 in downtrend, RSI confirmed",
        suggested_sl=Decimal("77500"),
        suggested_tp=Decimal("76500"),
    )
    proc._handle_trade_signal(signal, candle, new_id(), proc.all_candles)

    assert any(d.decision_type.value == "broker_capability_denied" for d in proc.decisions)
    assert not any(d.decision_type.value == "risk_approved" for d in proc.decisions)
    store.save_order_intent.assert_not_called()


def test_live_sim_eth_short_capability_denied_no_pending():
    store = MagicMock()
    account = {
        "id": "acc-1",
        "equity": "10000",
        "cash": "10000",
        "starting_cash": "10000",
        "is_active": True,
        "pending_owner_reset": False,
        "risk_settings": {
            "risk_per_trade_pct": 1.0,
            "max_total_open_sl_risk_pct": 3.0,
            "max_symbol_sl_risk_pct": 1.0,
            "max_group_sl_risk_pct": 2.0,
        },
    }
    store.session.execute.return_value.mappings.return_value.first.return_value = account
    store.build_currency_context_for_instruments.return_value = MagicMock(
        fx_rates=FxRateTable.usd_only()
    )

    signal_ts = datetime(2026, 9, 12, 18, 0, tzinfo=timezone.utc)
    candle = Candle(
        instrument_id="eth-id",
        timeframe="5m",
        timestamp=signal_ts,
        open=Decimal("2500"),
        high=Decimal("2510"),
        low=Decimal("2490"),
        close=Decimal("2505"),
        volume=Decimal("1"),
    )
    eth = Instrument(
        id="eth-id",
        symbol="ETHUSD",
        name="ETH/USD",
        asset_class="crypto",
        quote_currency="USD",
        quantity_step=Decimal("0.0001"),
        min_quantity=Decimal("0.0001"),
    )
    instance = MagicMock()
    instance.id = "inst-1"
    instance.timeframe = "5m"
    instance.strategy_slug = "gold-trend-pullback"
    instance.strategy_version = "1.0.0"

    from quantara_engine.domain.types import Signal, SignalAction

    signal = Signal(
        action=SignalAction.SELL,
        reason="Rally to EMA20 in downtrend, RSI confirmed",
        suggested_sl=Decimal("2550"),
        suggested_tp=Decimal("2450"),
    )

    with patch("quantara_engine.live_sim.allocator.find_allocation_by_canonical", return_value=None):
        with patch("quantara_engine.live_sim.allocator.log_allocation") as log_alloc:
            result = maybe_allocate_live_sim(
                store,
                entry={"instance": instance},
                instrument=eth,
                candle=candle,
                candles=[candle, candle],
                candle_index=0,
                signal=signal,
                execution_now=signal_ts + timedelta(minutes=5),
            )

    assert result["status"] == "rejected"
    assert result["reason"] == "BROKER_CAPABILITY_DENIED"
    log_alloc.assert_called_once()
    assert log_alloc.call_args.kwargs["accepted"] is False


def test_account_slug_resolves_live_sim_profile():
    cap = check_entry_capability_for_account(LIVE_SIM_10K_ACCOUNT_SLUG, _btc(), "short")
    assert not cap.allowed
    assert cap.account_slug == LIVE_SIM_10K_ACCOUNT_SLUG


def test_non_competition_portfolio_skips_capability_gate():
    cap = check_entry_capability_for_portfolio("non-competition-id", _btc(), "short")
    assert cap.allowed
