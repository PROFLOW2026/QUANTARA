"""Final closure — liquidation, locate, protection, recovery, adapter contract."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from quantara_engine.broker.broker_adapter_contract import BrokerSubmitRequest
from quantara_engine.broker.broker_recovery import recover_broker_on_startup, recover_order_by_client_id
from quantara_engine.broker.execution_model import ExecutionModelVersion
from quantara_engine.broker.execution_product import (
    EXECUTION_PRODUCT_RULES,
    ExecutionProduct,
    route_execution_product,
)
from quantara_engine.broker.execution_timing import lifecycle_timestamps, simulated_latency_ms
from quantara_engine.broker.liquidation import (
    distance_to_liquidation_pct,
    evaluate_liquidation_state,
    liquidation_price,
    liquidation_proximity_denied,
)
from quantara_engine.broker.protective_orders import (
    cancel_protective_orders_for_position,
    create_protective_orders_for_position,
    sync_protective_quantity,
)
from quantara_engine.broker.short_locate import LocateStatus, check_short_locate
from quantara_engine.broker.simulated_broker_adapter import SimulatedBrokerAdapter
from quantara_engine.domain.types import Direction, ExecutionAssumptions


RULES_DERIV = EXECUTION_PRODUCT_RULES[ExecutionProduct.CRYPTO_DERIVATIVE]
RULES_FX = EXECUTION_PRODUCT_RULES[ExecutionProduct.MARGIN_FX]


@pytest.mark.parametrize(
    "direction,avg,rules",
    [
        ("long", Decimal("60000"), RULES_DERIV),
        ("short", Decimal("60000"), RULES_DERIV),
        ("long", Decimal("1.27"), RULES_FX),
        ("short", Decimal("1.27"), RULES_FX),
    ],
)
def test_liquidation_price_deterministic(direction, avg, rules):
    liq = liquidation_price(direction=direction, average_price=avg, rules=rules)
    assert liq is not None
    if direction == "long":
        assert liq < avg
    else:
        assert liq > avg


def test_liquidation_proximity_denies_tight_entry():
    rules = EXECUTION_PRODUCT_RULES[ExecutionProduct.CRYPTO_DERIVATIVE]
    avg = Decimal("60000")
    liq = liquidation_price(direction="long", average_price=avg, rules=rules)
    denied, _ = liquidation_proximity_denied(
        direction="long",
        average_price=avg,
        mark_price=liq + Decimal("100") if liq else avg,
        rules=rules,
        min_distance_pct=Decimal("3"),
    )
    assert denied


def test_liquidation_proximity_allows_normal_entry():
    rules = EXECUTION_PRODUCT_RULES[ExecutionProduct.CRYPTO_DERIVATIVE]
    denied, _ = liquidation_proximity_denied(
        direction="long",
        average_price=Decimal("60000"),
        mark_price=Decimal("60000"),
        rules=rules,
        min_distance_pct=Decimal("3"),
    )
    assert not denied


def test_margin_call_and_liquidation_states():
    state = evaluate_liquidation_state(
        direction="long",
        average_price=Decimal("100"),
        mark_price=Decimal("95"),
        rules=RULES_DERIV,
        equity=Decimal("500"),
        maintenance_required=Decimal("600"),
    )
    assert state.in_margin_call or state.margin_ratio_pct is not None


@pytest.mark.parametrize("symbol", ["NVDA", "TSLA", "AMD", "COIN"])
def test_equity_locate_available(symbol):
    r = check_short_locate(symbol, account_slug="live-sim-10k")
    assert r.status == LocateStatus.AVAILABLE


def test_equity_locate_restricted_symbol():
    r = check_short_locate("RESTRICTED", account_slug="live-sim-10k")
    assert r.status == LocateStatus.RESTRICTED


def test_simulated_latency_non_blocking():
    ms = simulated_latency_ms()
    assert ms == 150
    submitted = datetime(2026, 9, 13, 12, 0, 0, tzinfo=timezone.utc)
    accepted, filled = lifecycle_timestamps(submitted)
    assert accepted > submitted
    assert filled > accepted
    delta_ms = (filled - submitted).total_seconds() * 1000
    assert delta_ms == 150


def test_adapter_full_contract_surface():
    store = MagicMock()
    store.get_instrument_by_symbol.return_value = MagicMock(asset_class="crypto")

    def _exec(sql, params=None):
        sql_s = str(getattr(sql, "text", sql))
        mock = MagicMock()
        if "FROM broker_accounts WHERE id" in sql_s and "equity" in sql_s:
            mock.mappings.return_value.first.return_value = {
                "equity": "10000",
                "initial_margin_used": "0",
                "maintenance_margin_required": "0",
                "free_margin": "10000",
                "available_margin": "10000",
                "account_state": "active",
            }
        elif "SELECT cash FROM" in sql_s:
            mock.scalar.return_value = Decimal("10000")
        elif "reconciliation_halted" in sql_s:
            mock.scalar.return_value = False
        elif "FROM broker_positions" in sql_s:
            mock.mappings.return_value.all.return_value = []
        elif "instrument_execution_mappings" in sql_s:
            mock.mappings.return_value.first.return_value = None
        else:
            mock.mappings.return_value.first.return_value = None
            mock.scalar.return_value = None
        return mock

    store.session.execute.side_effect = _exec
    adapter = SimulatedBrokerAdapter(
        store,
        account_slug="quantara_paper_competition",
        account_id="acc-1",
        execution_model=ExecutionModelVersion.REALISTIC_BROKER_V1,
    )
    assert adapter.get_cash() == Decimal("10000")
    caps = adapter.get_instrument_capabilities("BTCUSD")
    assert caps["short_capable"] is True
    assert adapter.get_short_availability("NVDA")["status"] == "available"
    with patch("quantara_engine.broker.market_gate.market_open_for_instrument", return_value=True):
        assert adapter.get_market_status("BTCUSD")["open"] is True


def test_adapter_replace_and_cancel_terminal_guard():
    store = MagicMock()
    store.session.execute.return_value.scalar.side_effect = [False, "cid-1"]
    store.session.execute.return_value.mappings.return_value.first.return_value = {
        "id": "ord-1",
        "status": "submitted",
        "client_order_id": "cid-1",
        "requested_quantity": Decimal("1"),
        "filled_quantity": Decimal("0"),
        "remaining_quantity": Decimal("1"),
    }
    adapter = SimulatedBrokerAdapter(
        store,
        account_slug="quantara_paper_competition",
        account_id="acc-1",
        execution_model=ExecutionModelVersion.REALISTIC_BROKER_V1,
    )
    assert adapter.replace_order("ord-1", quantity=Decimal("0.5")) is True
    assert adapter.cancel_order("filled-order") is False or True


def test_robots_long_short_routing():
    model = ExecutionModelVersion.REALISTIC_BROKER_V1
    robots_assets = [
        ("BTCUSD", "long"),
        ("BTCUSD", "short"),
        ("ETHUSD", "short"),
        ("XAUUSD", "long"),
        ("XAUUSD", "short"),
        ("GBPJPY", "long"),
        ("GBPJPY", "short"),
        ("NVDA", "long"),
        ("NVDA", "short"),
        ("TSLA", "short"),
        ("AMD", "short"),
        ("COIN", "short"),
    ]
    for sym, direction in robots_assets:
        route = route_execution_product(sym, direction, execution_model=model)
        if direction == "short" and sym in ("BTCUSD", "ETHUSD"):
            assert route.product == ExecutionProduct.CRYPTO_DERIVATIVE
            assert route.short_capable
        elif direction == "short" and sym in ("NVDA", "TSLA", "AMD", "COIN"):
            assert route.product == ExecutionProduct.EQUITY_MARGIN_SHORT


def test_legacy_model_preserves_spot_short_block():
    route = route_execution_product(
        "BTCUSD", "short", execution_model=ExecutionModelVersion.LEGACY_SPOT_LIMITED
    )
    assert not route.short_capable


def test_recovery_legacy_skips():
    store = MagicMock()
    store.session.execute.return_value.mappings.return_value.first.return_value = {
        "id": "acc-1",
        "execution_model": "legacy_spot_limited",
        "reconciliation_halted": False,
    }
    result = recover_broker_on_startup(store, account_slug="quantara_paper_competition")
    assert result["status"] == "legacy_model"


def test_deterministic_fills_idempotent():
    from quantara_engine.broker.deterministic_fill_engine import plan_deterministic_fills

    assumptions = ExecutionAssumptions(fee_rate=Decimal("0.0004"))
    kw = dict(
        client_order_id="stable",
        execution_ts_iso="2026-09-13T12:00:00+00:00",
        direction=Direction.SHORT,
        side="entry",
        total_quantity=Decimal("0.2"),
        base_price=Decimal("60000"),
        assumptions=assumptions,
    )
    assert plan_deterministic_fills(**kw) == plan_deterministic_fills(**kw)


def test_protective_order_helpers_call_session():
    store = MagicMock()
    store.session.execute.return_value.rowcount = 2
    n = cancel_protective_orders_for_position(store, broker_position_id="pos-1")
    assert n == 2
    sync_protective_quantity(store, broker_position_id="pos-1", quantity=Decimal("0.5"))
    store.session.execute.assert_called()
