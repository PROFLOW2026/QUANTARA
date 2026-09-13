"""Multi-broker Live Sim execution wiring — routed accounts, derivative parity, legacy guard."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock

import pytest
from sqlalchemy import text

from quantara_engine.broker.accounts import (
    LIVE_SIM_10K_ACCOUNT_SLUG,
    LIVE_SIM_IBKR_LIKE_SLUG,
    LIVE_SIM_KRAKEN_LIKE_SLUG,
)
from quantara_engine.broker.cost_accrual import accrue_position_costs
from quantara_engine.broker.execution_bridge import execute_through_broker
from quantara_engine.broker.execution_model import ExecutionModelVersion
from quantara_engine.broker.execution_product import ExecutionProduct, route_execution_product
from quantara_engine.broker.fees import load_fee_profile
from quantara_engine.broker.instrument_mapping import resolve_instrument_mapping_for_route
from quantara_engine.broker.liquidation import liquidation_price
from quantara_engine.broker.margin_profiles import load_margin_profile
from quantara_engine.broker.routing import route_to_broker
from quantara_engine.broker.vendor import BrokerVendor
from quantara_engine.domain.types import Direction, Instrument
from quantara_engine.execution.fill_calculator import FillResult
from quantara_engine.live_sim.execution_routing import (
    assert_live_sim_execution_target_allowed,
    legacy_execution_blocked,
    resolve_live_sim_execution_route,
    resolve_live_sim_runtime_context,
)
from quantara_engine.owner_portfolio.aggregation import aggregate_owner_portfolio
from quantara_engine.owner_portfolio.asset_allocation import (
    configure_equal_asset_allocations,
    deactivate_equal_asset_allocations,
    is_equal_asset_mode_active,
)
from quantara_engine.owner_portfolio.service import LIVE_SIM_OWNER_SLUG
from quantara_engine.broker.accounts import LIVE_SIM_VIRTUAL_PORTFOLIO_ID


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


def _instrument(symbol: str, asset_class: str) -> Instrument:
    return Instrument(
        id=f"{symbol.lower()}-id",
        symbol=symbol,
        name=symbol,
        asset_class=asset_class,
        quote_currency="USD",
        quantity_step=Decimal("0.0001") if asset_class == "crypto" else Decimal("1"),
        min_quantity=Decimal("0.0001") if asset_class == "crypto" else Decimal("1"),
    )


@pytest.mark.parametrize(
    "symbol,direction,expected_slug,expected_product",
    [
        ("BTCUSD", "long", LIVE_SIM_KRAKEN_LIKE_SLUG, ExecutionProduct.CRYPTO_DERIVATIVE),
        ("BTCUSD", "short", LIVE_SIM_KRAKEN_LIKE_SLUG, ExecutionProduct.CRYPTO_DERIVATIVE),
        ("ETHUSD", "long", LIVE_SIM_KRAKEN_LIKE_SLUG, ExecutionProduct.CRYPTO_DERIVATIVE),
        ("ETHUSD", "short", LIVE_SIM_KRAKEN_LIKE_SLUG, ExecutionProduct.CRYPTO_DERIVATIVE),
        ("NVDA", "long", LIVE_SIM_IBKR_LIKE_SLUG, ExecutionProduct.EQUITY_CASH),
        ("NVDA", "short", LIVE_SIM_IBKR_LIKE_SLUG, ExecutionProduct.EQUITY_MARGIN_SHORT),
        ("TSLA", "long", LIVE_SIM_IBKR_LIKE_SLUG, ExecutionProduct.EQUITY_CASH),
        ("AMD", "long", LIVE_SIM_IBKR_LIKE_SLUG, ExecutionProduct.EQUITY_CASH),
        ("COIN", "long", LIVE_SIM_IBKR_LIKE_SLUG, ExecutionProduct.EQUITY_CASH),
        ("GBPJPY", "long", LIVE_SIM_IBKR_LIKE_SLUG, ExecutionProduct.MARGIN_FX),
        ("XAUUSD", "long", LIVE_SIM_IBKR_LIKE_SLUG, ExecutionProduct.MARGIN_GOLD),
    ],
)
def test_live_sim_routing_active_equal_asset(
    active_equal_asset, symbol, direction, expected_slug, expected_product
):
    store = active_equal_asset
    ctx = resolve_live_sim_runtime_context(store, symbol, direction)
    assert ctx.account_slug == expected_slug
    assert ctx.route is not None
    assert ctx.route.execution_product == expected_product
    assert ctx.route.broker_account_slug == expected_slug


@pytest.mark.parametrize("symbol,direction", [("BTCUSD", "long"), ("BTCUSD", "short"), ("ETHUSD", "long"), ("ETHUSD", "short")])
def test_kraken_derivative_fee_margin_funding_liquidation(active_equal_asset, symbol, direction):
    store = active_equal_asset
    route = resolve_live_sim_execution_route(store, symbol, direction)
    fee = load_fee_profile(
        store,
        broker_vendor=BrokerVendor.KRAKEN,
        execution_product=route.execution_product,
    )
    assert fee is not None
    assert fee.taker_rate == Decimal("0.0005")
    assert fee.maker_rate == Decimal("0.0002")
    margin = load_margin_profile(
        store,
        broker_vendor=BrokerVendor.KRAKEN,
        execution_product=route.execution_product,
        instrument_symbol=symbol,
    )
    assert margin is not None
    mapping = resolve_instrument_mapping_for_route(
        store,
        symbol=symbol,
        broker_vendor=BrokerVendor.KRAKEN.value,
        execution_product=route.execution_product,
    )
    assert mapping is not None
    assert mapping.broker_product_id in ("PF_XBTUSD", "PF_ETHUSD")
    rules = margin.rules
    liq = liquidation_price(
        direction=direction,
        average_price=Decimal("50000") if symbol == "BTCUSD" else Decimal("2500"),
        rules=rules,
    )
    assert liq > 0


def test_signed_crypto_funding_long_pays_short_receives(active_equal_asset):
    store = active_equal_asset
    aid = store.session.execute(
        text("SELECT id::text FROM broker_accounts WHERE slug = :slug"),
        {"slug": LIVE_SIM_KRAKEN_LIKE_SLUG},
    ).scalar()
    pos_id = "00000000-0000-4000-8000-000000000099"
    store.session.execute(
        text(
            """
            INSERT INTO broker_positions (
              id, broker_account_id, instrument_id, net_quantity, average_price, mark_price,
              execution_product, accumulated_funding, updated_at
            )
            SELECT CAST(:pid AS uuid), CAST(:aid AS uuid), i.id,
                   :qty, 50000, 50000, 'crypto_derivative', 0, NOW() - INTERVAL '8 hours'
            FROM instruments i WHERE i.symbol = 'BTCUSD'
            ON CONFLICT (id) DO UPDATE SET
              net_quantity = EXCLUDED.net_quantity,
              mark_price = EXCLUDED.mark_price,
              updated_at = NOW() - INTERVAL '8 hours'
            """
        ),
        {"pid": pos_id, "aid": aid, "qty": Decimal("0.01")},
    )
    store.session.execute(
        text(
            """
            UPDATE broker_accounts SET cash = 2500, balance = 2500 WHERE id = CAST(:aid AS uuid)
            """
        ),
        {"aid": aid},
    )
    store.session.flush()
    cash_before = Decimal(
        str(
            store.session.execute(
                text("SELECT cash FROM broker_accounts WHERE id = CAST(:aid AS uuid)"),
                {"aid": aid},
            ).scalar()
        )
    )
    long_debit = accrue_position_costs(
        store,
        broker_account_id=str(aid),
        broker_position_id=pos_id,
        symbol="BTCUSD",
        net_quantity=Decimal("0.01"),
        mark_price=Decimal("50000"),
        execution_product=ExecutionProduct.CRYPTO_DERIVATIVE.value,
        fx_rates={"USD": Decimal("1")},
        now=datetime(2026, 9, 13, 12, 0, tzinfo=timezone.utc),
    )
    assert long_debit > 0
    store.session.execute(
        text(
            """
            UPDATE broker_positions
            SET net_quantity = -0.01, updated_at = NOW() - INTERVAL '8 hours'
            WHERE id = CAST(:pid AS uuid)
            """
        ),
        {"pid": pos_id},
    )
    cash_mid = Decimal(
        str(
            store.session.execute(
                text("SELECT cash FROM broker_accounts WHERE id = CAST(:aid AS uuid)"),
                {"aid": aid},
            ).scalar()
        )
    )
    short_credit = accrue_position_costs(
        store,
        broker_account_id=str(aid),
        broker_position_id=pos_id,
        symbol="BTCUSD",
        net_quantity=Decimal("-0.01"),
        mark_price=Decimal("50000"),
        execution_product=ExecutionProduct.CRYPTO_DERIVATIVE.value,
        fx_rates={"USD": Decimal("1")},
        now=datetime(2026, 9, 13, 20, 0, tzinfo=timezone.utc),
    )
    assert short_credit < 0
    cash_after = Decimal(
        str(
            store.session.execute(
                text("SELECT cash FROM broker_accounts WHERE id = CAST(:aid AS uuid)"),
                {"aid": aid},
            ).scalar()
        )
    )
    assert cash_after > cash_mid
    store.session.rollback()


def test_legacy_account_blocked_while_multi_broker_active(active_equal_asset):
    store = active_equal_asset
    assert legacy_execution_blocked(store, LIVE_SIM_10K_ACCOUNT_SLUG)
    allowed, reason = assert_live_sim_execution_target_allowed(store, LIVE_SIM_10K_ACCOUNT_SLUG)
    assert not allowed
    assert reason == "legacy_live_sim_execution_disabled"


def test_legacy_execute_through_broker_rejected(active_equal_asset):
    store = active_equal_asset
    btc = _instrument("BTCUSD", "crypto")
    fill = FillResult(
        fill_price=Decimal("50000"),
        base_price=Decimal("50000"),
        spread_cost=Decimal("0"),
        slippage=Decimal("0"),
        fees=Decimal("1"),
    )
    result = execute_through_broker(
        store,
        portfolio_id=LIVE_SIM_VIRTUAL_PORTFOLIO_ID,
        instrument=btc,
        direction=Direction.LONG,
        quantity=Decimal("0.001"),
        fill=fill,
        execution_at=datetime(2026, 9, 13, 12, 0, tzinfo=timezone.utc),
        timeframe="15m",
        idempotency_key="test:legacy:block",
        skip_if_not_competition=False,
        account_slug=LIVE_SIM_10K_ACCOUNT_SLUG,
    )
    assert result is not None
    assert not result.accepted
    store.session.rollback()


def test_owner_aggregate_ten_thousand_no_legacy_double_count(active_equal_asset):
    store = active_equal_asset
    snap = aggregate_owner_portfolio(store, slug=LIVE_SIM_OWNER_SLUG)
    assert snap is not None
    assert snap.total_equity == Decimal("10000")
    enabled_slugs = {s.slug for s in snap.broker_slices if s.enabled}
    assert LIVE_SIM_10K_ACCOUNT_SLUG not in enabled_slugs
    assert LIVE_SIM_IBKR_LIKE_SLUG in enabled_slugs
    assert LIVE_SIM_KRAKEN_LIKE_SLUG in enabled_slugs


def test_vendor_execution_model_realistic_after_activation(active_equal_asset):
    store = active_equal_asset
    rows = store.session.execute(
        text(
            """
            SELECT slug, execution_model::text
            FROM broker_accounts
            WHERE slug IN (:ibkr, :kraken)
            """
        ),
        {"ibkr": LIVE_SIM_IBKR_LIKE_SLUG, "kraken": LIVE_SIM_KRAKEN_LIKE_SLUG},
    ).mappings().all()
    by_slug = {r["slug"]: r["execution_model"] for r in rows}
    assert by_slug[LIVE_SIM_IBKR_LIKE_SLUG] == ExecutionModelVersion.REALISTIC_BROKER_V1.value
    assert by_slug[LIVE_SIM_KRAKEN_LIKE_SLUG] == ExecutionModelVersion.REALISTIC_BROKER_V1.value


def test_btc_long_product_realistic():
    route = route_execution_product(
        "BTCUSD",
        "long",
        execution_model=ExecutionModelVersion.REALISTIC_BROKER_V1,
    )
    assert route.product == ExecutionProduct.CRYPTO_DERIVATIVE


def test_resolve_live_sim_broker_slug_not_legacy_when_multi_broker():
    store = MagicMock()
    store.session.execute.return_value.mappings.return_value.first.return_value = {
        "id": "p1",
        "multi_broker_mode_enabled": True,
    }
    store.session.execute.return_value.scalar.return_value = LIVE_SIM_IBKR_LIKE_SLUG
    from quantara_engine.owner_portfolio.service import OwnerPortfolioService

    slug = OwnerPortfolioService(store).resolve_live_sim_broker_account_slug()
    assert slug == LIVE_SIM_IBKR_LIKE_SLUG
