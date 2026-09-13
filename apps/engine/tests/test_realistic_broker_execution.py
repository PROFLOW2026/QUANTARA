"""Realistic broker execution — product router, lifecycle, idempotency."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from quantara_engine.broker.capability import check_entry_capability_for_account, entry_direction_allowed
from quantara_engine.broker.client_order_id import derive_client_order_id
from quantara_engine.broker.deterministic_fill_engine import plan_deterministic_fills
from quantara_engine.broker.execution_model import ExecutionModelVersion
from quantara_engine.broker.execution_product import ExecutionProduct, route_execution_product
from quantara_engine.broker.live_execution_gate import external_broker_submission_allowed
from quantara_engine.broker.pre_trade import evaluate_broker_order
from quantara_engine.broker.profile import QUANTARA_STANDARD_PAPER
from quantara_engine.broker.simulated_broker_engine import SimulatedBrokerEngine
from quantara_engine.broker.types import BrokerOrderRequest, BrokerRejectionReason
from quantara_engine.domain.types import Direction, ExecutionAssumptions
from quantara_engine.broker.account import build_account_snapshot
from quantara_engine.broker.types import BrokerAccountSnapshot


def _empty_account(starting: Decimal = Decimal("320000")) -> BrokerAccountSnapshot:
    return build_account_snapshot(
        cash=starting,
        balance=starting,
        realized_pnl=Decimal("0"),
        positions={},
        fx_rates={"USD": Decimal("1"), "JPY": Decimal("150")},
    )


def _btc():
    from quantara_engine.domain.types import Instrument

    return Instrument(
        id="btc-id",
        symbol="BTCUSD",
        name="BTC/USD",
        asset_class="crypto",
        quote_currency="USD",
        quantity_step=Decimal("0.0001"),
        min_quantity=Decimal("0.0001"),
    )


def test_external_broker_hard_disabled():
    assert external_broker_submission_allowed() is False


def test_btc_short_routes_to_crypto_derivative_realistic():
    route = route_execution_product(
        "BTCUSD",
        "short",
        execution_model=ExecutionModelVersion.REALISTIC_BROKER_V1,
    )
    assert route.product == ExecutionProduct.CRYPTO_DERIVATIVE
    assert route.short_capable


def test_btc_long_routes_to_crypto_derivative_realistic():
    route = route_execution_product(
        "BTCUSD",
        "long",
        execution_model=ExecutionModelVersion.REALISTIC_BROKER_V1,
    )
    assert route.product == ExecutionProduct.CRYPTO_DERIVATIVE


def test_eth_long_routes_to_crypto_derivative_realistic():
    route = route_execution_product(
        "ETHUSD",
        "long",
        execution_model=ExecutionModelVersion.REALISTIC_BROKER_V1,
    )
    assert route.product == ExecutionProduct.CRYPTO_DERIVATIVE


def test_btc_short_legacy_still_spot_blocked():
    route = route_execution_product(
        "BTCUSD",
        "short",
        execution_model=ExecutionModelVersion.LEGACY_SPOT_LIMITED,
    )
    assert not route.short_capable


def test_crypto_short_allowed_realistic_capability():
    cap = entry_direction_allowed(
        QUANTARA_STANDARD_PAPER,
        "crypto",
        "short",
        symbol="BTCUSD",
        execution_model=ExecutionModelVersion.REALISTIC_BROKER_V1,
    )
    assert cap.allowed
    assert cap.execution_product == ExecutionProduct.CRYPTO_DERIVATIVE.value


def test_crypto_short_pre_trade_realistic_product_rules():
    account = _empty_account()
    route = route_execution_product(
        "BTCUSD",
        "short",
        execution_model=ExecutionModelVersion.REALISTIC_BROKER_V1,
    )
    req = BrokerOrderRequest(
        symbol="BTCUSD",
        asset_class="crypto",
        direction="short",
        quantity=Decimal("0.01"),
        mark_price=Decimal("60000"),
        execution_product=route.product.value,
        product_rules_key=route.asset_class_key,
    )
    decision = evaluate_broker_order(
        account, QUANTARA_STANDARD_PAPER, req, {"USD": Decimal("1")}, product_rules=route.rules
    )
    assert decision.accepted
    assert decision.rejection_reason != BrokerRejectionReason.SHORT_NOT_ALLOWED


def test_client_order_id_stable():
    a = derive_client_order_id(account_slug="live-sim-10k", idempotency_key="opp:123")
    b = derive_client_order_id(account_slug="live-sim-10k", idempotency_key="opp:123")
    c = derive_client_order_id(account_slug="live-sim-10k", idempotency_key="opp:124")
    assert a == b
    assert a != c


def test_deterministic_fills_reproducible():
    assumptions = ExecutionAssumptions(
        spread=Decimal("30"),
        slippage_pct=Decimal("0.0001"),
        fee_rate=Decimal("0.0004"),
    )
    kwargs = dict(
        client_order_id="abc123",
        execution_ts_iso="2026-09-13T12:00:00+00:00",
        direction=Direction.SHORT,
        side="entry",
        total_quantity=Decimal("0.1"),
        base_price=Decimal("60000"),
        assumptions=assumptions,
        allow_partial=True,
    )
    a = plan_deterministic_fills(**kwargs)
    b = plan_deterministic_fills(**kwargs)
    assert len(a) == len(b)
    assert [s.quantity for s in a] == [s.quantity for s in b]


def test_simulated_broker_idempotent_client_order_id():
    engine = SimulatedBrokerEngine(execution_model=ExecutionModelVersion.REALISTIC_BROKER_V1)
    from quantara_engine.broker.broker_adapter_contract import BrokerSubmitRequest

    assumptions = ExecutionAssumptions(
        spread=Decimal("30"),
        slippage_pct=Decimal("0.0001"),
        fee_rate=Decimal("0.0004"),
    )
    req = BrokerSubmitRequest(
        client_order_id="stable-id-1",
        account_slug="quantara_paper_competition",
        symbol="BTCUSD",
        direction="short",
        quantity=Decimal("0.01"),
        mark_price=Decimal("60000"),
    )
    at = datetime(2026, 9, 13, 12, 0, tzinfo=timezone.utc)
    r1 = engine.submit_order(req, pre_accepted=True, assumptions=assumptions, execution_at=at)
    r2 = engine.submit_order(req, pre_accepted=True, assumptions=assumptions, execution_at=at)
    assert r1.broker_order_id == r2.broker_order_id
    assert r1.accepted


def test_simulated_broker_cancel_before_fill_complete():
    engine = SimulatedBrokerEngine(execution_model=ExecutionModelVersion.REALISTIC_BROKER_V1)
    order_id = "fake-order"
    assert engine.cancel_order(order_id) is False


def test_equity_short_routes_margin_product():
    route = route_execution_product(
        "NVDA",
        "short",
        execution_model=ExecutionModelVersion.REALISTIC_BROKER_V1,
    )
    assert route.product == ExecutionProduct.EQUITY_MARGIN_SHORT


def test_lost_response_recovery_via_client_order_id():
    engine = SimulatedBrokerEngine(execution_model=ExecutionModelVersion.REALISTIC_BROKER_V1)
    from quantara_engine.broker.broker_adapter_contract import BrokerSubmitRequest

    assumptions = ExecutionAssumptions(fee_rate=Decimal("0.0004"))
    req = BrokerSubmitRequest(
        client_order_id="lost-response-1",
        account_slug="quantara_paper_competition",
        symbol="ETHUSD",
        direction="short",
        quantity=Decimal("0.05"),
        mark_price=Decimal("2500"),
    )
    at = datetime(2026, 9, 13, 12, 0, tzinfo=timezone.utc)
    lost = engine.submit_order(
        req,
        pre_accepted=True,
        assumptions=assumptions,
        execution_at=at,
        simulate_lost_response=True,
    )
    assert lost.submission_unknown
    recovered = engine.recover_submission("lost-response-1")
    assert recovered is not None
    assert recovered.broker_order_id == lost.broker_order_id


def test_capability_with_legacy_store_mock():
    store = MagicMock()
    store.session.execute.return_value.mappings.return_value.first.return_value = {
        "execution_model": "legacy_spot_limited",
    }
    cap = check_entry_capability_for_account(
        "live-sim-10k",
        _btc(),
        "short",
        store=store,
    )
    assert not cap.allowed
