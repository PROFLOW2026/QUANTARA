"""Multi-broker foundation — owner portfolio, routing, fees, global risk."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from quantara_engine.broker.client_order_id import derive_client_order_id
from quantara_engine.broker.connector_registry import BrokerConnectorRegistry
from quantara_engine.broker.vendor import BrokerVendor
from quantara_engine.broker.execution_product import ExecutionProduct
from quantara_engine.broker.fees import FeeModel, calculate_commission
from quantara_engine.broker.routing import route_to_broker
from quantara_engine.broker.execution_model import ExecutionModelVersion
from quantara_engine.owner_portfolio.global_risk import evaluate_owner_global_risk, symbol_risk_group
from quantara_engine.owner_portfolio.service import validate_allocation_sum
from quantara_engine.risk.concentration import RISK_GROUPS


def test_allocation_cannot_exceed_target():
    v = validate_allocation_sum(Decimal("10000"), {"ibkr": Decimal("6000"), "kraken": Decimal("5000")})
    assert not v.valid
    assert v.message == "allocated_capital_exceeds_target"


def test_allocation_6k_4k_equals_10k():
    v = validate_allocation_sum(Decimal("10000"), {"ibkr": Decimal("6000"), "kraken": Decimal("4000")})
    assert v.valid
    assert v.allocated_sum == Decimal("10000")
    assert v.remaining == Decimal("0")


def test_ibkr_equity_minimum_commission():
    model = FeeModel(
        fee_kind="per_share",
        per_share_rate=Decimal("0.0035"),
        minimum_per_order=Decimal("0.35"),
    )
    # ~4 shares @ $140 = $560 notional
    result = calculate_commission(
        fee_model=model, notional=Decimal("560"), quantity=Decimal("4")
    )
    assert result.commission == Decimal("0.35")
    assert result.minimum_applied


def test_ibkr_fx_two_dollar_minimum():
    model = FeeModel(
        fee_kind="bps_notional",
        bps_rate=Decimal("0.20"),
        minimum_per_order=Decimal("2.00"),
    )
    result = calculate_commission(
        fee_model=model, notional=Decimal("500"), quantity=Decimal("1")
    )
    assert result.commission == Decimal("2.00")
    assert result.minimum_applied


def test_kraken_taker_percentage():
    model = FeeModel(
        fee_kind="percentage_notional",
        taker_rate=Decimal("0.0005"),
        maker_rate=Decimal("0.0002"),
    )
    result = calculate_commission(
        fee_model=model, notional=Decimal("1000"), quantity=Decimal("1")
    )
    assert result.commission == Decimal("0.50")


def test_kraken_maker_percentage():
    from quantara_engine.broker.fees import FeeSide

    model = FeeModel(
        fee_kind="percentage_notional",
        taker_rate=Decimal("0.0005"),
        maker_rate=Decimal("0.0002"),
    )
    result = calculate_commission(
        fee_model=model,
        notional=Decimal("1000"),
        quantity=Decimal("1"),
        side=FeeSide.MAKER,
    )
    assert result.commission == Decimal("0.20")


def test_crypto_risk_group_btc_and_coin():
    assert symbol_risk_group("BTCUSD") == "CRYPTO_RISK"
    assert symbol_risk_group("COIN") == "CRYPTO_RISK"
    assert "BTCUSD" in RISK_GROUPS["CRYPTO_RISK"]
    assert "COIN" in RISK_GROUPS["CRYPTO_RISK"]


def test_client_order_id_collision_safe_across_accounts():
    key = "entry:opp-123"
    a = derive_client_order_id(account_slug="live-sim-ibkr-like", idempotency_key=key)
    b = derive_client_order_id(account_slug="live-sim-kraken-like", idempotency_key=key)
    assert a != b


def test_connector_registry_multiple_instances():
    reg = BrokerConnectorRegistry()
    reg.register(account_slug="ibkr", account_id="1", broker_vendor=BrokerVendor.IBKR)
    reg.register(account_slug="kraken", account_id="2", broker_vendor=BrokerVendor.KRAKEN)
    assert len(reg.list_instances()) == 2
    assert reg.get("ibkr") is not None
    assert reg.get("kraken") is not None


def test_global_risk_skipped_legacy_single_account():
    store = MagicMock()
    store.session.execute.return_value.mappings.return_value.first.return_value = {
        "id": "p1",
        "target_capital": Decimal("10000"),
        "multi_broker_mode_enabled": False,
        "global_execution_halted": False,
        "risk_settings": {},
    }
    verdict = evaluate_owner_global_risk(
        store,
        owner_slug="live-sim-owner",
        symbol="BTCUSD",
        incremental_sl_risk_usd=Decimal("50"),
    )
    assert verdict.allowed


def test_routing_btc_to_kraken_from_db_rules(broker_test_store):
    decision = route_to_broker(
        broker_test_store,
        symbol="BTCUSD",
        direction="short",
        execution_model=ExecutionModelVersion.REALISTIC_BROKER_V1,
    )
    assert decision.broker_vendor.value == "KRAKEN"
    assert decision.execution_product == ExecutionProduct.CRYPTO_DERIVATIVE


def test_routing_nvda_to_ibkr(broker_test_store):
    decision = route_to_broker(
        broker_test_store,
        symbol="NVDA",
        direction="long",
        execution_model=ExecutionModelVersion.REALISTIC_BROKER_V1,
    )
    assert decision.broker_vendor.value == "IBKR"
    assert decision.execution_product == ExecutionProduct.EQUITY_CASH


def test_multiple_mappings_per_canonical_symbol(broker_test_store):
    from quantara_engine.broker.instrument_mapping import load_all_mappings_for_symbol

    mappings = load_all_mappings_for_symbol(broker_test_store, "BTCUSD")
    vendors = {m.broker_symbol for m in mappings}
    assert len(mappings) >= 2
    assert len(vendors) >= 2


def test_global_equity_aggregation_no_double_count(broker_test_store):
    from quantara_engine.owner_portfolio.aggregation import aggregate_owner_portfolio

    snap = aggregate_owner_portfolio(broker_test_store, slug="live-sim-owner")
    assert snap is not None
    assert snap.total_equity == sum(s.equity for s in snap.broker_slices if s.enabled)
    assert snap.total_realized_pnl == sum(s.realized_pnl for s in snap.broker_slices if s.enabled)


def test_broker_execution_isolation_gate():
    from quantara_engine.broker.reconciliation_orchestrator import is_broker_execution_allowed

    store = MagicMock()
    store.session.execute.return_value.mappings.return_value.first.return_value = {
        "reconciliation_halted": True,
        "connection_state": "CONNECTED",
        "global_execution_halted": False,
    }
    assert is_broker_execution_allowed(store, "live-sim-kraken-like") is False


def test_research_routing_unchanged_legacy():
    from quantara_engine.broker.routing import legacy_execution_product_route

    route = legacy_execution_product_route(
        "NVDA",
        "long",
        execution_model=ExecutionModelVersion.REALISTIC_BROKER_V1,
    )
    assert route.product == ExecutionProduct.EQUITY_CASH
