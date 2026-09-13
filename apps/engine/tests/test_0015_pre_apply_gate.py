"""0015 pre-apply gate — DB invariants, mappings, virtual accounts, backward compat."""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from quantara_engine.broker.execution_product import ExecutionProduct
from quantara_engine.broker.fees import load_fee_profile
from quantara_engine.broker.instrument_mapping import (
    load_instrument_mapping,
    load_legacy_simulated_mapping,
    resolve_instrument_mapping_for_route,
)
from quantara_engine.broker.live_execution_gate import REAL_BROKER_SUBMISSION_ENABLED
from quantara_engine.broker.vendor import BrokerVendor
from quantara_engine.owner_portfolio.global_risk import evaluate_owner_global_risk
from quantara_engine.owner_portfolio.service import LIVE_SIM_OWNER_SLUG, OwnerPortfolioService


def _portfolio_id(store) -> str:
    return store.session.execute(
        text("SELECT id::text FROM owner_trading_portfolios WHERE slug = :slug"),
        {"slug": LIVE_SIM_OWNER_SLUG},
    ).scalar()


def _legacy_link_id(store) -> str:
    return store.session.execute(
        text(
            """
            SELECT pba.id::text
            FROM portfolio_broker_accounts pba
            JOIN broker_accounts ba ON ba.id = pba.broker_account_id
            WHERE ba.slug = 'live-sim-10k'
            """
        )
    ).scalar()


def test_migration_applied_cleanly(broker_test_store):
    tables = broker_test_store.session.execute(
        text(
            """
            SELECT table_name FROM information_schema.tables
            WHERE table_schema = 'public'
              AND table_name IN (
                'owner_trading_portfolios', 'portfolio_broker_accounts',
                'broker_routing_rules', 'broker_fee_profiles'
              )
            """
        )
    ).scalars().all()
    assert len(tables) == 4


def test_db_rejects_negative_allocation(broker_test_store):
    pid = _portfolio_id(broker_test_store)
    ba_id = broker_test_store.session.execute(
        text("SELECT id::text FROM broker_accounts WHERE slug = 'live-sim-ibkr-like'")
    ).scalar()
    if not ba_id:
        OwnerPortfolioService(broker_test_store).configure_multi_broker_allocations(
            LIVE_SIM_OWNER_SLUG, ibkr_allocation=Decimal("0"), kraken_allocation=Decimal("0")
        )
        ba_id = broker_test_store.session.execute(
            text("SELECT id::text FROM broker_accounts WHERE slug = 'live-sim-ibkr-like'")
        ).scalar()
    with pytest.raises(DBAPIError):
        broker_test_store.session.execute(
            text(
                """
                UPDATE portfolio_broker_accounts
                SET allocated_capital = -1
                WHERE owner_portfolio_id = CAST(:pid AS uuid)
                  AND broker_account_id = CAST(:aid AS uuid)
                """
            ),
            {"pid": pid, "aid": ba_id},
        )
        broker_test_store.session.commit()
    broker_test_store.session.rollback()


def test_db_rejects_over_allocation_when_enabled(broker_test_store):
    pid = _portfolio_id(broker_test_store)
    svc = OwnerPortfolioService(broker_test_store)
    svc.configure_multi_broker_allocations(
        LIVE_SIM_OWNER_SLUG, ibkr_allocation=Decimal("6000"), kraken_allocation=Decimal("4000")
    )
    ibkr_link = broker_test_store.session.execute(
        text(
            """
            SELECT pba.id::text FROM portfolio_broker_accounts pba
            JOIN broker_accounts ba ON ba.id = pba.broker_account_id
            WHERE pba.owner_portfolio_id = CAST(:pid AS uuid) AND ba.slug = 'live-sim-ibkr-like'
            """
        ),
        {"pid": pid},
    ).scalar()
    with pytest.raises(DBAPIError):
        broker_test_store.session.execute(
            text(
                """
                UPDATE portfolio_broker_accounts
                SET enabled = TRUE, allocated_capital = 11000
                WHERE id = CAST(:id AS uuid)
                """
            ),
            {"id": ibkr_link},
        )
        broker_test_store.session.commit()
    broker_test_store.session.rollback()


def test_db_rejects_target_capital_below_enabled_allocations(broker_test_store):
    pid = _portfolio_id(broker_test_store)
    with pytest.raises(DBAPIError):
        broker_test_store.session.execute(
            text(
                """
                UPDATE owner_trading_portfolios
                SET target_capital = 5000
                WHERE id = CAST(:pid AS uuid)
                """
            ),
            {"pid": pid},
        )
        broker_test_store.session.commit()
    broker_test_store.session.rollback()


def test_new_vendor_account_starts_disconnected(broker_test_store):
    svc = OwnerPortfolioService(broker_test_store)
    result = svc.configure_multi_broker_allocations(
        LIVE_SIM_OWNER_SLUG, ibkr_allocation=Decimal("6000"), kraken_allocation=Decimal("4000")
    )
    assert result["ok"]
    row = broker_test_store.session.execute(
        text(
            """
            SELECT connection_state::text, broker_environment::text, is_active
            FROM broker_accounts WHERE slug = 'live-sim-kraken-like'
            """
        )
    ).mappings().first()
    assert row["connection_state"] == "DISCONNECTED"
    assert row["broker_environment"] == "SIMULATION"
    assert row["is_active"] is False


def test_existing_simulated_brokers_connected(broker_test_store):
    rows = broker_test_store.session.execute(
        text(
            """
            SELECT slug, connection_state::text
            FROM broker_accounts
            WHERE slug IN ('quantara_paper_competition', 'live-sim-10k')
            """
        )
    ).mappings().all()
    by_slug = {r["slug"]: r["connection_state"] for r in rows}
    assert by_slug["quantara_paper_competition"] == "CONNECTED"
    assert by_slug["live-sim-10k"] == "CONNECTED"


def test_legacy_simulated_nvda_mapping_single_row(broker_test_store):
    m = load_legacy_simulated_mapping(broker_test_store, "NVDA")
    assert m.execution_product == ExecutionProduct.EQUITY_CASH
    assert m.broker_symbol == "NVDA"


def test_ibkr_equity_long_and_short_mappings(broker_test_store):
    long_m = resolve_instrument_mapping_for_route(
        broker_test_store,
        symbol="NVDA",
        broker_vendor="IBKR",
        execution_product=ExecutionProduct.EQUITY_CASH,
    )
    short_m = resolve_instrument_mapping_for_route(
        broker_test_store,
        symbol="NVDA",
        broker_vendor="IBKR",
        execution_product=ExecutionProduct.EQUITY_MARGIN_SHORT,
    )
    assert long_m.execution_product == ExecutionProduct.EQUITY_CASH
    assert short_m.execution_product == ExecutionProduct.EQUITY_MARGIN_SHORT
    for sym in ("TSLA", "AMD", "COIN"):
        resolve_instrument_mapping_for_route(
            broker_test_store, symbol=sym, broker_vendor="IBKR", execution_product=ExecutionProduct.EQUITY_CASH
        )
        resolve_instrument_mapping_for_route(
            broker_test_store,
            symbol=sym,
            broker_vendor="IBKR",
            execution_product=ExecutionProduct.EQUITY_MARGIN_SHORT,
        )


def test_kraken_btc_eth_derivative_mappings(broker_test_store):
    for sym in ("BTCUSD", "ETHUSD"):
        m = resolve_instrument_mapping_for_route(
            broker_test_store,
            symbol=sym,
            broker_vendor="KRAKEN",
            execution_product=ExecutionProduct.CRYPTO_DERIVATIVE,
        )
        assert m.execution_product == ExecutionProduct.CRYPTO_DERIVATIVE
        assert m.broker_product_id is not None


def test_xau_gold_uses_notional_fee_not_per_contract(broker_test_store):
    fee = load_fee_profile(
        broker_test_store,
        broker_vendor=BrokerVendor.IBKR,
        execution_product=ExecutionProduct.MARGIN_GOLD,
    )
    assert fee is not None
    assert fee.fee_kind == "bps_notional"
    assert fee.minimum_per_order == Decimal("2.00")


def test_multi_row_mapping_legacy_default_still_works(broker_test_store):
    """Without product filter, SIMULATED returns deterministic first row (Research path)."""
    m = load_instrument_mapping(broker_test_store, "BTCUSD", broker_vendor="SIMULATED")
    assert m.execution_product in (ExecutionProduct.CRYPTO_SPOT, ExecutionProduct.CRYPTO_DERIVATIVE)


def test_fresh_db_virtual_accounts_via_allocation_settings(broker_test_store):
    svc = OwnerPortfolioService(broker_test_store)
    before = broker_test_store.session.execute(
        text("SELECT COUNT(*) FROM broker_accounts WHERE slug LIKE 'live-sim-%like'")
    ).scalar()
    result = svc.configure_multi_broker_allocations(
        LIVE_SIM_OWNER_SLUG, ibkr_allocation=Decimal("6000"), kraken_allocation=Decimal("4000")
    )
    assert result["ok"]
    assert result.get("activated") is False
    after = broker_test_store.session.execute(
        text("SELECT COUNT(*) FROM broker_accounts WHERE slug LIKE 'live-sim-%like'")
    ).scalar()
    assert after >= before
    portfolio = svc.get_portfolio_row(LIVE_SIM_OWNER_SLUG)
    assert portfolio["multi_broker_mode_enabled"] is False


def test_cannot_activate_without_exact_target(broker_test_store):
    svc = OwnerPortfolioService(broker_test_store)
    result = svc.configure_multi_broker_allocations(
        LIVE_SIM_OWNER_SLUG,
        ibkr_allocation=Decimal("6000"),
        kraken_allocation=Decimal("3000"),
        activate=True,
    )
    assert result["ok"] is False
    assert result["error"] == "allocation_must_equal_target"


def _vendor_link_id(store, vendor_slug: str) -> str:
    pid = _portfolio_id(store)
    return store.session.execute(
        text(
            """
            SELECT pba.id::text
            FROM portfolio_broker_accounts pba
            JOIN broker_accounts ba ON ba.id = pba.broker_account_id
            WHERE pba.owner_portfolio_id = CAST(:pid AS uuid)
              AND ba.slug = :slug
            """
        ),
        {"pid": pid, "slug": vendor_slug},
    ).scalar()


def _reset_live_sim_single_broker_state(store) -> None:
    pid = _portfolio_id(store)
    store.session.execute(
        text(
            """
            UPDATE owner_trading_portfolios
            SET multi_broker_mode_enabled = FALSE, updated_at = NOW()
            WHERE id = CAST(:pid AS uuid)
            """
        ),
        {"pid": pid},
    )
    store.session.execute(
        text(
            """
            UPDATE portfolio_broker_accounts
            SET enabled = FALSE, updated_at = NOW()
            WHERE owner_portfolio_id = CAST(:pid AS uuid)
              AND NOT is_legacy_primary
            """
        ),
        {"pid": pid},
    )
    store.session.execute(
        text(
            """
            UPDATE portfolio_broker_accounts
            SET enabled = TRUE, updated_at = NOW()
            WHERE owner_portfolio_id = CAST(:pid AS uuid)
              AND is_legacy_primary
            """
        ),
        {"pid": pid},
    )
    store.session.commit()


def test_db_rejects_multi_broker_activation_legacy_only(broker_test_store):
    _reset_live_sim_single_broker_state(broker_test_store)
    pid = _portfolio_id(broker_test_store)
    with pytest.raises(DBAPIError) as exc_info:
        broker_test_store.session.execute(
            text(
                """
                UPDATE owner_trading_portfolios
                SET multi_broker_mode_enabled = TRUE
                WHERE id = CAST(:pid AS uuid)
                """
            ),
            {"pid": pid},
        )
        broker_test_store.session.commit()
    broker_test_store.session.rollback()
    assert "legacy primary" in str(exc_info.value).lower()


def test_db_rejects_multi_broker_activation_incomplete_allocation(broker_test_store):
    _reset_live_sim_single_broker_state(broker_test_store)
    svc = OwnerPortfolioService(broker_test_store)
    svc.configure_multi_broker_allocations(
        LIVE_SIM_OWNER_SLUG, ibkr_allocation=Decimal("6000"), kraken_allocation=Decimal("3000")
    )
    pid = _portfolio_id(broker_test_store)
    legacy_id = _legacy_link_id(broker_test_store)
    ibkr_id = _vendor_link_id(broker_test_store, "live-sim-ibkr-like")
    broker_test_store.session.execute(
        text("UPDATE portfolio_broker_accounts SET enabled = FALSE WHERE id = CAST(:id AS uuid)"),
        {"id": legacy_id},
    )
    broker_test_store.session.execute(
        text(
            """
            UPDATE portfolio_broker_accounts
            SET enabled = TRUE, allocated_capital = 6000
            WHERE id = CAST(:id AS uuid)
            """
        ),
        {"id": ibkr_id},
    )
    with pytest.raises(DBAPIError) as exc_info:
        broker_test_store.session.execute(
            text(
                """
                UPDATE owner_trading_portfolios
                SET multi_broker_mode_enabled = TRUE
                WHERE id = CAST(:pid AS uuid)
                """
            ),
            {"pid": pid},
        )
        broker_test_store.session.commit()
    broker_test_store.session.rollback()
    msg = str(exc_info.value)
    assert "equal target_capital" in msg.lower()
    assert "%s" not in msg


def test_db_rejects_multi_broker_activation_over_allocation(broker_test_store):
    _reset_live_sim_single_broker_state(broker_test_store)
    svc = OwnerPortfolioService(broker_test_store)
    svc.configure_multi_broker_allocations(
        LIVE_SIM_OWNER_SLUG, ibkr_allocation=Decimal("7000"), kraken_allocation=Decimal("4000")
    )
    pid = _portfolio_id(broker_test_store)
    legacy_id = _legacy_link_id(broker_test_store)
    ibkr_id = _vendor_link_id(broker_test_store, "live-sim-ibkr-like")
    kraken_id = _vendor_link_id(broker_test_store, "live-sim-kraken-like")
    broker_test_store.session.execute(
        text(
            "ALTER TABLE portfolio_broker_accounts DISABLE TRIGGER portfolio_broker_accounts_allocation_invariant"
        )
    )
    broker_test_store.session.execute(
        text("UPDATE portfolio_broker_accounts SET enabled = FALSE WHERE id = CAST(:id AS uuid)"),
        {"id": legacy_id},
    )
    broker_test_store.session.execute(
        text(
            """
            UPDATE portfolio_broker_accounts
            SET enabled = TRUE, allocated_capital = 7000
            WHERE id = CAST(:id AS uuid)
            """
        ),
        {"id": ibkr_id},
    )
    broker_test_store.session.execute(
        text(
            """
            UPDATE portfolio_broker_accounts
            SET enabled = TRUE, allocated_capital = 4000
            WHERE id = CAST(:id AS uuid)
            """
        ),
        {"id": kraken_id},
    )
    broker_test_store.session.execute(
        text(
            "ALTER TABLE portfolio_broker_accounts ENABLE TRIGGER portfolio_broker_accounts_allocation_invariant"
        )
    )
    with pytest.raises(DBAPIError) as exc_info:
        broker_test_store.session.execute(
            text(
                """
                UPDATE owner_trading_portfolios
                SET multi_broker_mode_enabled = TRUE
                WHERE id = CAST(:pid AS uuid)
                """
            ),
            {"pid": pid},
        )
        broker_test_store.session.commit()
    broker_test_store.session.rollback()
    msg = str(exc_info.value)
    assert "equal target_capital" in msg.lower()
    assert "11000.00" in msg
    assert "%s" not in msg


def test_valid_ibkr_kraken_activation_passes(broker_test_store):
    _reset_live_sim_single_broker_state(broker_test_store)
    svc = OwnerPortfolioService(broker_test_store)
    result = svc.configure_multi_broker_allocations(
        LIVE_SIM_OWNER_SLUG,
        ibkr_allocation=Decimal("6000"),
        kraken_allocation=Decimal("4000"),
        activate=True,
    )
    assert result["ok"] is True
    assert result.get("activated") is True
    portfolio = svc.get_portfolio_row(LIVE_SIM_OWNER_SLUG)
    assert portfolio["multi_broker_mode_enabled"] is True
    vendors = broker_test_store.session.execute(
        text(
            """
            SELECT ba.slug, ba.broker_vendor::text, pba.enabled, pba.allocated_capital
            FROM portfolio_broker_accounts pba
            JOIN broker_accounts ba ON ba.id = pba.broker_account_id
            WHERE pba.owner_portfolio_id = CAST(:pid AS uuid)
              AND NOT pba.is_legacy_primary
            ORDER BY ba.slug
            """
        ),
        {"pid": portfolio["id"]},
    ).mappings().all()
    assert len(vendors) == 2
    assert all(v["enabled"] for v in vendors)
    assert sum(v["allocated_capital"] for v in vendors) == Decimal("10000")
    vendor_set = {v["broker_vendor"] for v in vendors}
    assert vendor_set == {"IBKR", "KRAKEN"}
    _reset_live_sim_single_broker_state(broker_test_store)


def test_negative_allocation_rejected_at_app_layer(broker_test_store):
    svc = OwnerPortfolioService(broker_test_store)
    result = svc.configure_multi_broker_allocations(
        LIVE_SIM_OWNER_SLUG,
        ibkr_allocation=Decimal("-1"),
        kraken_allocation=Decimal("0"),
    )
    assert result["ok"] is False
    assert result["error"] == "negative_allocation_not_allowed"


def test_global_risk_off_while_multi_broker_off(broker_test_store):
    verdict = evaluate_owner_global_risk(
        broker_test_store,
        owner_slug=LIVE_SIM_OWNER_SLUG,
        symbol="BTCUSD",
        incremental_sl_risk_usd=Decimal("9999"),
    )
    assert verdict.allowed
    assert verdict.reason == "legacy_single_account"


def test_real_submission_still_disabled():
    assert REAL_BROKER_SUBMISSION_ENABLED is False
