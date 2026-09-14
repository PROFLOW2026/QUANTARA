"""Live Sim execution unblock — 4-tuple resume + crypto_derivative economics."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from quantara_engine.broker.accounts import LIVE_SIM_KRAKEN_LIKE_SLUG
from quantara_engine.broker.execution_model import ExecutionModelVersion
from quantara_engine.broker.execution_product import ExecutionProduct, route_execution_product
from quantara_engine.broker.profile import QUANTARA_LIVE_SIM_10K
from quantara_engine.domain.types import Direction, Instrument
from quantara_engine.execution.cost_profile import execution_assumptions_for
from quantara_engine.live_sim.sizing import (
    size_live_sim_entry,
    validate_live_sim_broker_pre_trade,
)
from quantara_engine.portfolio.currency import FxRateTable
from quantara_engine.risk.sizing import select_quantity_for_risk_budget

TZ3 = timezone(timedelta(hours=3))


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


@pytest.mark.parametrize("symbol,direction", [
    ("BTCUSD", "long"),
    ("BTCUSD", "short"),
    ("ETHUSD", "long"),
    ("ETHUSD", "short"),
])
def test_kraken_crypto_routes_to_derivative(symbol: str, direction: str):
    route = route_execution_product(
        symbol,
        direction,
        execution_model=ExecutionModelVersion.REALISTIC_BROKER_V1,
    )
    assert route.product == ExecutionProduct.CRYPTO_DERIVATIVE
    assert route.asset_class_key == "crypto_derivative"
    assert route.short_capable is True
    assert route.rules.shorting_allowed is True
    assert route.rules.initial_margin_pct == Decimal("10")
    assert route.rules.max_leverage == Decimal("10")


def test_legacy_spot_crypto_short_not_capable():
    route = route_execution_product(
        "BTCUSD",
        "short",
        execution_model=ExecutionModelVersion.LEGACY_SPOT_LIMITED,
    )
    assert route.product == ExecutionProduct.CRYPTO_SPOT
    assert route.short_capable is False
    assert route.rules.shorting_allowed is False


@pytest.mark.parametrize("instrument_factory,direction", [
    (_btc, Direction.LONG),
    (_btc, Direction.SHORT),
    (_eth, Direction.LONG),
    (_eth, Direction.SHORT),
])
def test_kraken_crypto_pre_trade_accepts_long_and_short(instrument_factory, direction):
    instrument = instrument_factory()
    entry = Decimal("50000") if instrument.symbol == "BTCUSD" else Decimal("2500")
    sl = entry * Decimal("0.99") if direction == Direction.LONG else entry * Decimal("1.01")
    assumptions = execution_assumptions_for(instrument, entry)
    from quantara_engine.live_sim.sizing import entry_mark_price_for_sizing

    mark = entry_mark_price_for_sizing(entry, direction, assumptions)
    route = route_execution_product(
        instrument.symbol,
        "long" if direction == Direction.LONG else "short",
        execution_model=ExecutionModelVersion.REALISTIC_BROKER_V1,
    )
    sizing = size_live_sim_entry(
        equity=Decimal("1250"),
        cash=Decimal("1250"),
        target_risk=Decimal("12.50"),
        entry_reference=entry,
        stop_loss=sl,
        direction=direction,
        instrument=instrument,
        fx_rates=FxRateTable.usd_only(),
        execution_assumptions=assumptions,
        max_asset_leverage=route.rules.max_leverage,
        product_rules=route.rules,
        hard_max_risk_usd=Decimal("12.50"),
    )
    assert sizing.deny_reason is None
    assert sizing.quantity > 0

    accepted, reason = validate_live_sim_broker_pre_trade(
        equity=Decimal("1250"),
        cash=Decimal("1250"),
        spot_crypto_cash=Decimal("1250"),
        quantity=sizing.quantity,
        mark_price=mark,
        direction=direction,
        instrument=instrument,
        profile=QUANTARA_LIVE_SIM_10K,
        fx_rates=FxRateTable.usd_only(),
        account_slug=LIVE_SIM_KRAKEN_LIKE_SLUG,
        execution_model=ExecutionModelVersion.REALISTIC_BROKER_V1,
    )
    assert accepted is True, reason
    assert reason is None


def test_spot_crypto_short_still_rejected_without_derivative_route():
    eth = _eth()
    accepted, reason = validate_live_sim_broker_pre_trade(
        equity=Decimal("10000"),
        cash=Decimal("10000"),
        spot_crypto_cash=Decimal("10000"),
        quantity=Decimal("0.1"),
        mark_price=Decimal("2500"),
        direction=Direction.SHORT,
        instrument=eth,
        profile=QUANTARA_LIVE_SIM_10K,
        fx_rates=FxRateTable.usd_only(),
        # No account_slug / model → instrument crypto spot rules
    )
    assert accepted is False
    assert reason == "short_not_allowed"


def test_hard_max_risk_prevents_symbol_sl_overshoot():
    """Sizing must step down under hard gate budget (no 2% tolerance overshoot)."""
    xau = _xau()
    entry = Decimal("2650")
    sl = Decimal("2640")  # $10 SL distance
    assumptions = execution_assumptions_for(xau, entry)
    equity = Decimal("1250")
    target = equity * Decimal("0.01")  # 12.50
    # Tolerance alone would allow ~12.75; hard max keeps gate-safe.
    hard = target

    sizing = size_live_sim_entry(
        equity=equity,
        cash=equity,
        target_risk=target,
        entry_reference=entry,
        stop_loss=sl,
        direction=Direction.LONG,
        instrument=xau,
        fx_rates=FxRateTable.usd_only(),
        execution_assumptions=assumptions,
        max_asset_leverage=Decimal("10"),
        hard_max_risk_usd=hard,
    )
    assert sizing.deny_reason is None or sizing.quantity == 0
    if sizing.quantity > 0:
        assert sizing.expected_risk_usd <= hard


def test_resume_pending_consumes_four_tuple_equal_asset_context():
    """Regression: old 3-unpack crashed with ValueError when equal-asset context returned 4."""
    from quantara_engine.live_sim.allocator import _resume_pending_allocation

    store = MagicMock()
    instrument = _eth()
    signal_ts = datetime(2026, 9, 14, 1, 0, tzinfo=TZ3)
    exec_ts = datetime(2026, 9, 14, 1, 15, tzinfo=TZ3)
    now = datetime(2026, 9, 14, 1, 30, 5, tzinfo=TZ3)
    exec_candle = MagicMock()
    exec_candle.close = Decimal("2500")
    exec_candle.timestamp = exec_ts

    existing = {
        "id": "log-1",
        "canonical_opportunity_key": "canon-1",
        "opportunity_key": "opp-1",
        "strategy_slug": "btc-rsi-mean-reversion",
        "strategy_version": "1.0.0",
        "robot_label": "Robot A",
        "symbol": "ETHUSD",
        "timeframe": "15m",
        "direction": "long",
        "signal_candle_timestamp": signal_ts,
        "proposed_entry": Decimal("2500"),
        "stop_loss": Decimal("2475"),
        "take_profit": Decimal("2550"),
        "metadata": {
            "pending_execution": True,
            "lifecycle_state": "pending_execution",
            "execution_candle_timestamp": exec_ts.isoformat(),
        },
    }
    account = {
        "id": "acct-kraken",
        "slug": LIVE_SIM_KRAKEN_LIKE_SLUG,
        "equity": Decimal("2500"),
        "starting_cash": Decimal("2500"),
        "cash": Decimal("2500"),
        "spot_crypto_cash": Decimal("2500"),
        "is_active": True,
        "risk_settings": {},
    }
    asset_row = {
        "id": "asset-eth",
        "enabled": True,
        "broker_account_id": "acct-kraken",
        "equity": Decimal("1250"),
        "cash": Decimal("1250"),
        "risk_settings": {},
    }
    entry = {
        "instance": type(
            "Inst",
            (),
            {"id": "pf", "strategy_slug": "btc-rsi-mean-reversion", "timeframe": "15m"},
        )()
    }

    four_tuple = (
        Decimal("1250"),
        Decimal("1250"),
        "acct-kraken",
        asset_row,
    )

    with patch(
        "quantara_engine.live_sim.allocator.allocation_lifecycle_state",
        return_value="pending_execution",
    ), patch(
        "quantara_engine.live_sim.allocator.resolve_execution_candle",
        return_value=(exec_candle, exec_ts),
    ), patch(
        "quantara_engine.live_sim.allocator.is_execution_candle_ready",
        return_value=(True, None),
    ), patch(
        "quantara_engine.live_sim.allocator.resolve_live_sim_runtime_context",
    ) as runtime_ctx, patch(
        "quantara_engine.broker.capability.check_entry_capability_for_account",
    ) as cap, patch(
        "quantara_engine.live_sim.allocator._equal_asset_sizing_context",
        return_value=four_tuple,
    ), patch(
        "quantara_engine.live_sim.allocator.broker_account_row_by_id",
        return_value=account,
    ), patch(
        "quantara_engine.live_sim.allocator.load_asset_gate_settings",
    ) as load_asset, patch(
        "quantara_engine.live_sim.allocator.maybe_roll_asset_daily_start",
    ) as roll, patch(
        "quantara_engine.live_sim.allocator.update_asset_high_water_mark",
    ), patch(
        "quantara_engine.live_sim.allocator.compute_open_sl_risk",
    ) as open_risk, patch(
        "quantara_engine.live_sim.allocator._current_asset_notional_usd",
        return_value=Decimal("0"),
    ), patch(
        "quantara_engine.live_sim.allocator._execute_accepted_allocation",
        return_value={"status": "accepted", "log_id": "log-1", "position_id": "pos-1"},
    ) as execute:
        from quantara_engine.live_sim.risk_policy import LiveSimRiskSettings, OpenRiskSnapshot

        runtime = MagicMock()
        runtime.legacy_blocked = False
        runtime.account = account
        runtime.account_id = "acct-kraken"
        runtime.account_slug = LIVE_SIM_KRAKEN_LIKE_SLUG
        runtime_ctx.return_value = runtime
        cap.return_value = MagicMock(allowed=True, reason=None)
        settings = LiveSimRiskSettings(
            risk_per_trade_pct=Decimal("1"),
            max_total_open_sl_risk_pct=Decimal("3"),
            max_symbol_sl_risk_pct=Decimal("1"),
            max_group_sl_risk_pct=Decimal("2"),
            daily_loss_gate_pct=Decimal("2"),
            max_drawdown_gate_pct=Decimal("10"),
            concentration_mode="ENFORCE",
            high_water_mark=Decimal("1250"),
            daily_start_equity=Decimal("1250"),
            daily_start_date="2026-09-14",
        )
        load_asset.return_value = settings
        roll.return_value = settings
        open_risk.return_value = OpenRiskSnapshot(
            total_sl_risk_usd=Decimal("0"),
            by_symbol={},
            by_group={},
        )
        store.build_currency_context_for_instruments.return_value = MagicMock(
            fx_rates=FxRateTable.usd_only()
        )

        result = _resume_pending_allocation(
            store,
            existing=existing,
            account_id="acct-kraken",
            account=account,
            entry=entry,
            instrument=instrument,
            candles=[exec_candle],
            execution_now=now,
        )

    assert result["status"] == "accepted"
    execute.assert_called_once()


def test_old_three_unpack_would_fail_on_four_tuple():
    """Document the pre-fix crash shape — 4-tuple cannot unpack into 3."""
    asset_ctx = (Decimal("1250"), Decimal("1250"), "acct", {"id": "a"})
    with pytest.raises(ValueError, match="too many values to unpack"):
        equity, cash, broker_id = asset_ctx  # noqa: F841
