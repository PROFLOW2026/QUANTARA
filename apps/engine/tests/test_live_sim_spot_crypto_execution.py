"""Live Sim spot crypto cash + broker-safe sizing execution fix."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from quantara_engine.broker.account import build_account_snapshot
from quantara_engine.broker.accounts import LIVE_SIM_10K_ACCOUNT_SLUG
from quantara_engine.broker.pre_trade import evaluate_broker_order
from quantara_engine.broker.profile import QUANTARA_LIVE_SIM_10K
from quantara_engine.broker.spot_crypto_cash import (
    effective_spot_crypto_cash,
    initial_spot_crypto_cash_for_account,
)
from quantara_engine.broker.types import BrokerOrderRequest, BrokerRejectionReason
from quantara_engine.domain.types import Direction, Instrument
from quantara_engine.execution.cost_profile import execution_assumptions_for
from quantara_engine.live_sim.account_bootstrap import repair_pristine_live_sim_spot_crypto_cash
from quantara_engine.live_sim.sizing import (
    broker_quantized_asset_leverage,
    entry_mark_price_for_sizing,
    size_live_sim_entry,
    validate_live_sim_broker_pre_trade,
)
from quantara_engine.portfolio.currency import FxRateTable

TZ3 = timezone(timedelta(hours=3))


def _eth() -> Instrument:
    return Instrument(
        id="eth-id",
        symbol="ETHUSD",
        name="ETH/USD",
        asset_class="crypto",
        quote_currency="USD",
        quantity_step=Decimal("0.0001"),
        min_quantity=Decimal("0.0001"),
    )


def test_spot_crypto_cash_semantics_uninitialized_mirror():
    assert effective_spot_crypto_cash(cash=Decimal("10000"), spot_crypto_cash=Decimal("0")) == Decimal(
        "10000"
    )
    assert effective_spot_crypto_cash(cash=Decimal("5000"), spot_crypto_cash=Decimal("5000")) == Decimal(
        "5000"
    )
    assert effective_spot_crypto_cash(cash=Decimal("0"), spot_crypto_cash=Decimal("0")) == Decimal("0")


def test_initial_spot_crypto_cash_equals_starting_cash():
    assert initial_spot_crypto_cash_for_account(starting_cash=Decimal("10000")) == Decimal("10000")


def test_crypto_long_pre_trade_passes_with_effective_spot_cash():
    eth = _eth()
    entry = Decimal("2525")
    assumptions = execution_assumptions_for(eth, entry)
    mark = entry_mark_price_for_sizing(entry, Direction.LONG, assumptions)
    sizing = size_live_sim_entry(
        equity=Decimal("10000"),
        cash=Decimal("10000"),
        target_risk=Decimal("100"),
        entry_reference=entry,
        stop_loss=Decimal("2500"),
        direction=Direction.LONG,
        instrument=eth,
        fx_rates=FxRateTable.usd_only(),
        execution_assumptions=assumptions,
        max_asset_leverage=Decimal("1"),
    )
    accepted, reason = validate_live_sim_broker_pre_trade(
        equity=Decimal("10000"),
        cash=Decimal("10000"),
        spot_crypto_cash=Decimal("0"),
        quantity=sizing.quantity,
        mark_price=mark,
        direction=Direction.LONG,
        instrument=eth,
        profile=QUANTARA_LIVE_SIM_10K,
    )
    assert accepted is True
    assert reason is None
    assert broker_quantized_asset_leverage(sizing.quantity * mark, Decimal("10000")) <= Decimal("1")


def test_crypto_long_pre_trade_fails_without_effective_cash_when_both_zero():
    eth = _eth()
    entry = Decimal("2525")
    assumptions = execution_assumptions_for(eth, entry)
    mark = entry_mark_price_for_sizing(entry, Direction.LONG, assumptions)
    account = build_account_snapshot(
        cash=Decimal("0"),
        balance=Decimal("0"),
        realized_pnl=Decimal("0"),
        positions={},
        fx_rates={"USD": Decimal("1")},
        spot_crypto_cash=Decimal("0"),
    )
    decision = evaluate_broker_order(
        account,
        QUANTARA_LIVE_SIM_10K,
        BrokerOrderRequest(
            symbol="ETHUSD",
            asset_class="crypto",
            direction="long",
            quantity=Decimal("1"),
            mark_price=mark,
            data_fresh=True,
            market_open=True,
        ),
        {"USD": Decimal("1")},
    )
    assert not decision.accepted
    assert decision.rejection_reason in (
        BrokerRejectionReason.INSUFFICIENT_BUYING_POWER,
        BrokerRejectionReason.INSUFFICIENT_MARGIN,
    )


def test_crypto_short_capability_denied():
    eth = _eth()
    from quantara_engine.broker.capability import check_entry_capability_for_account

    cap = check_entry_capability_for_account(LIVE_SIM_10K_ACCOUNT_SLUG, eth, "short")
    assert not cap.allowed


def test_safe_sizing_stays_below_max_asset_exposure():
    eth = _eth()
    entry = Decimal("2525")
    assumptions = execution_assumptions_for(eth, entry)
    mark = entry_mark_price_for_sizing(entry, Direction.LONG, assumptions)
    sizing = size_live_sim_entry(
        equity=Decimal("10000"),
        cash=Decimal("10000"),
        target_risk=Decimal("100"),
        entry_reference=entry,
        stop_loss=Decimal("2500"),
        direction=Direction.LONG,
        instrument=eth,
        fx_rates=FxRateTable.usd_only(),
        execution_assumptions=assumptions,
        max_asset_leverage=Decimal("1"),
    )
    notional = sizing.quantity * mark
    assert notional < Decimal("10000")
    assert broker_quantized_asset_leverage(notional, Decimal("10000")) <= Decimal("1")


def test_repair_pristine_live_sim_account_only_when_zero_fill():
    store = MagicMock()
    pristine = {
        "id": "acct-id",
        "slug": LIVE_SIM_10K_ACCOUNT_SLUG,
        "cash": Decimal("10000"),
        "spot_crypto_cash": Decimal("0"),
        "equity": Decimal("10000"),
        "starting_cash": Decimal("10000"),
        "realized_pnl": Decimal("0"),
        "gross_realized_pnl": Decimal("0"),
        "fees_paid": Decimal("0"),
        "broker_orders": 0,
        "broker_fills": 0,
        "broker_positions": 0,
        "live_sim_positions": 0,
    }
    store.session.execute.return_value.mappings.return_value.first.side_effect = [pristine, None]

    result = repair_pristine_live_sim_spot_crypto_cash(store)
    assert result is not None
    assert result["before"]["spot_crypto_cash"] == "0"
    assert result["after"]["spot_crypto_cash"] == "10000"
    store.session.execute.assert_called()


def test_repair_skips_account_with_fills():
    store = MagicMock()
    store.session.execute.return_value.mappings.return_value.first.return_value = {
        "id": "acct-id",
        "slug": LIVE_SIM_10K_ACCOUNT_SLUG,
        "cash": Decimal("10000"),
        "spot_crypto_cash": Decimal("0"),
        "equity": Decimal("10000"),
        "starting_cash": Decimal("10000"),
        "realized_pnl": Decimal("0"),
        "gross_realized_pnl": Decimal("0"),
        "fees_paid": Decimal("0"),
        "broker_orders": 1,
        "broker_fills": 1,
        "broker_positions": 0,
        "live_sim_positions": 0,
    }
    assert repair_pristine_live_sim_spot_crypto_cash(store) is None


def test_pending_resume_rejected_not_counted_as_expired():
    from quantara_engine.live_sim.allocator import resume_all_pending_live_sim_allocations

    store = MagicMock()
    store.build_currency_context_for_instruments.return_value = MagicMock(
        fx_rates=FxRateTable.usd_only()
    )
    pending_row = {
        "id": "log-1",
        "canonical_opportunity_key": "canon-1",
        "strategy_slug": "gold-trend-pullback",
        "symbol": "ETHUSD",
        "timeframe": "15m",
    }
    store.session.execute.return_value.mappings.return_value.all.return_value = [pending_row]

    signal_ts = datetime(2026, 9, 13, 12, 0, tzinfo=TZ3)
    exec_ts = datetime(2026, 9, 13, 12, 15, tzinfo=TZ3)
    after = datetime(2026, 9, 13, 12, 30, 32, tzinfo=TZ3)

    with patch(
        "quantara_engine.live_sim.allocator._account_row",
        return_value={
            "id": "acct",
            "is_active": True,
            "equity": 10000,
            "cash": 10000,
            "spot_crypto_cash": 10000,
            "starting_cash": 10000,
            "risk_settings": {},
        },
    ), patch(
        "quantara_engine.live_sim.allocator.repair_pristine_live_sim_spot_crypto_cash",
        return_value=None,
    ), patch(
        "quantara_engine.live_sim.allocator.expire_stale_live_sim_allocations",
        return_value=0,
    ), patch(
        "quantara_engine.live_sim.allocator.find_allocation_by_canonical",
        return_value={
            "id": "log-1",
            "canonical_opportunity_key": "canon-1",
            "strategy_slug": "gold-trend-pullback",
            "strategy_version": "1.0.0",
            "robot_label": "Robot A",
            "symbol": "ETHUSD",
            "timeframe": "15m",
            "direction": "long",
            "signal_candle_timestamp": signal_ts,
            "proposed_entry": Decimal("2525"),
            "stop_loss": Decimal("2500"),
            "take_profit": Decimal("2600"),
            "calculated_quantity": Decimal("3.96"),
            "calculated_risk_usd": Decimal("100"),
            "metadata": {
                "pending_execution": True,
                "execution_candle_timestamp": exec_ts.isoformat(),
            },
        },
    ), patch(
        "quantara_engine.live_sim.allocator._resume_pending_allocation",
        return_value={"status": "accepted", "log_id": "log-1", "position_id": "pos-1"},
    ):
        report = resume_all_pending_live_sim_allocations(store, after)

    assert report["pending_found"] == 1
    assert report["resumed"] == 1
    assert report["filled"] == 1
    assert report["expired"] == 0
