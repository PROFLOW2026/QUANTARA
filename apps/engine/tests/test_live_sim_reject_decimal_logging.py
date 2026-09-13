"""Regression: _reject() risk pct logging must not crash on Decimal equity."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from quantara_engine.domain.types import Candle, Instrument, Signal, SignalAction
from quantara_engine.owner_portfolio.asset_allocation import (
    configure_equal_asset_allocations,
    deactivate_equal_asset_allocations,
    is_equal_asset_mode_active,
)
from quantara_engine.owner_portfolio.service import LIVE_SIM_OWNER_SLUG
from quantara_engine.live_sim.allocator import maybe_allocate_live_sim
from quantara_engine.live_sim.risk_policy import GateResult, OpenRiskSnapshot
from quantara_engine.live_sim.sizing import LiveSimSizingResult
from quantara_engine.owner_portfolio.global_risk import GlobalRiskVerdict


@pytest.fixture
def active_equal_asset(broker_test_store):
    store = broker_test_store
    if is_equal_asset_mode_active(store, LIVE_SIM_OWNER_SLUG):
        deactivate_equal_asset_allocations(store, LIVE_SIM_OWNER_SLUG)
    result = configure_equal_asset_allocations(store, LIVE_SIM_OWNER_SLUG, activate=True)
    assert result["ok"], result
    try:
        yield store
    finally:
        store.session.rollback()
        deactivate_equal_asset_allocations(store, LIVE_SIM_OWNER_SLUG)


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


def _instance():
    inst = MagicMock()
    inst.id = "inst-1"
    inst.timeframe = "5m"
    inst.strategy_slug = "gold-trend-pullback"
    inst.strategy_version = "1.0.0"
    return inst


def _signal() -> Signal:
    return Signal(
        action=SignalAction.BUY,
        reason="test",
        suggested_sl=Decimal("76000"),
        suggested_tp=Decimal("78000"),
    )


def _candles(signal_ts: datetime) -> tuple[Candle, datetime]:
    candle = Candle(
        instrument_id="btc-id",
        timeframe="5m",
        timestamp=signal_ts,
        open=Decimal("77000"),
        high=Decimal("77100"),
        low=Decimal("76900"),
        close=Decimal("77000"),
        volume=Decimal("1"),
    )
    execution_now = signal_ts + timedelta(minutes=5)
    return candle, execution_now


def _default_sizing() -> LiveSimSizingResult:
    return LiveSimSizingResult(
        quantity=Decimal("0.0001"),
        expected_risk_usd=Decimal("12.50"),
        target_risk_usd=Decimal("12.50"),
        deny_reason=None,
        sizing_reason="risk_budget",
    )


def _run_rejection(
    active_equal_asset,
    *,
    open_risk: OpenRiskSnapshot,
    expected_reason: str,
    sizing: LiveSimSizingResult | None = None,
    gate_patch: GateResult | None = None,
    owner_patch: GlobalRiskVerdict | None = None,
    asset_patch=None,
):
    store = active_equal_asset
    signal_ts = datetime.now(timezone.utc).replace(second=0, microsecond=0) - timedelta(minutes=5)
    candle, execution_now = _candles(signal_ts)
    sizing = sizing or _default_sizing()

    patches = [
        patch(
            "quantara_engine.live_sim.allocator.compute_open_sl_risk",
            return_value=open_risk,
        ),
        patch(
            "quantara_engine.live_sim.allocator.size_live_sim_entry",
            return_value=sizing,
        ),
        patch(
            "quantara_engine.live_sim.allocator.find_allocation_by_canonical",
            return_value=None,
        ),
        patch(
            "quantara_engine.broker.reconciliation_orchestrator.is_broker_execution_allowed",
            return_value=True,
        ),
    ]
    if gate_patch is not None:
        patches.append(
            patch(
                "quantara_engine.live_sim.allocator.evaluate_entry_gates",
                return_value=gate_patch,
            )
        )
    if owner_patch is not None:
        patches.append(
            patch(
                "quantara_engine.owner_portfolio.global_risk.evaluate_owner_global_risk",
                return_value=owner_patch,
            )
        )
    if asset_patch is not None:
        patches.append(
            patch(
                "quantara_engine.owner_portfolio.asset_risk.evaluate_asset_envelope_risk",
                return_value=asset_patch,
            )
        )

    from contextlib import ExitStack

    with ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        result = maybe_allocate_live_sim(
            store,
            entry={"instance": _instance()},
            instrument=_btc(),
            candle=candle,
            candles=[candle, candle],
            candle_index=0,
            signal=_signal(),
            execution_now=execution_now,
        )

    assert result["status"] == "rejected"
    assert result["reason"] == expected_reason
    return result


def test_old_reject_pct_pattern_raises_type_error():
    """Document the exact failure mode that crashed strategy_runner."""
    equity = Decimal("1250")
    sym_risk = Decimal("12")
    expected_risk = Decimal("12.50")
    with pytest.raises(TypeError, match="unsupported operand type"):
        _ = float(sym_risk + expected_risk) / equity * 100


def test_fixed_reject_pct_pattern_decimal_equity():
    equity = Decimal("1250")
    sym_risk = Decimal("12")
    expected_risk = Decimal("12.50")
    sym_pct = float((sym_risk + expected_risk) / equity * 100)
    assert sym_pct == pytest.approx(1.96, rel=1e-3)


def test_reject_logging_symbol_sl_risk_limit(active_equal_asset):
    open_risk = OpenRiskSnapshot(
        total_sl_risk_usd=Decimal("12"),
        by_symbol={"BTCUSD": Decimal("12")},
        by_group={"CRYPTO_RISK": Decimal("12")},
    )
    _run_rejection(
        active_equal_asset,
        open_risk=open_risk,
        expected_reason="SYMBOL_SL_RISK_LIMIT",
        gate_patch=GateResult(False, "SYMBOL_SL_RISK_LIMIT", "symbol SL risk would exceed 1%"),
    )


def test_reject_logging_group_sl_risk_limit(active_equal_asset):
    open_risk = OpenRiskSnapshot(
        total_sl_risk_usd=Decimal("20"),
        by_symbol={"ETHUSD": Decimal("20")},
        by_group={"CRYPTO_RISK": Decimal("20")},
    )
    _run_rejection(
        active_equal_asset,
        open_risk=open_risk,
        expected_reason="GROUP_SL_RISK_LIMIT",
        gate_patch=GateResult(False, "GROUP_SL_RISK_LIMIT", "CRYPTO_RISK SL risk would exceed 2%"),
    )


def test_reject_logging_owner_global_risk(active_equal_asset):
    open_risk = OpenRiskSnapshot(
        total_sl_risk_usd=Decimal("0"),
        by_symbol={},
        by_group={},
    )
    _run_rejection(
        active_equal_asset,
        open_risk=open_risk,
        expected_reason="owner_max_total_sl_risk",
        gate_patch=GateResult(True),
        owner_patch=GlobalRiskVerdict(
            allowed=False,
            reason="owner_max_total_sl_risk",
            layer="owner_global",
        ),
    )


def test_reject_logging_asset_envelope_risk(active_equal_asset):
    from quantara_engine.owner_portfolio.asset_risk import AssetRiskVerdict

    open_risk = OpenRiskSnapshot(
        total_sl_risk_usd=Decimal("0"),
        by_symbol={},
        by_group={},
    )
    _run_rejection(
        active_equal_asset,
        open_risk=open_risk,
        expected_reason="asset_sl_risk_exceeds_envelope",
        gate_patch=GateResult(True),
        owner_patch=GlobalRiskVerdict(allowed=True, reason=None, layer="owner_global"),
        asset_patch=AssetRiskVerdict(allowed=False, reason="asset_sl_risk_exceeds_envelope"),
    )
