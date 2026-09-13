"""0018 active-mode guards — broker link disable/delete and target_capital drift."""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from quantara_engine.owner_portfolio.asset_allocation import (
    configure_equal_asset_allocations,
    deactivate_equal_asset_allocations,
    is_equal_asset_mode_active,
)
from quantara_engine.owner_portfolio.asset_ledger import apply_asset_fill_impact
from quantara_engine.owner_portfolio.service import LIVE_SIM_OWNER_SLUG


def _broker_link_id(store, vendor_slug: str) -> str:
    return store.session.execute(
        text(
            """
            SELECT pba.id::text
            FROM portfolio_broker_accounts pba
            JOIN broker_accounts ba ON ba.id = pba.broker_account_id
            JOIN owner_trading_portfolios otp ON otp.id = pba.owner_portfolio_id
            WHERE otp.slug = :slug AND ba.slug = :vendor
            """
        ),
        {"slug": LIVE_SIM_OWNER_SLUG, "vendor": vendor_slug},
    ).scalar()


def _portfolio_id(store) -> str:
    return store.session.execute(
        text("SELECT id::text FROM owner_trading_portfolios WHERE slug = :slug"),
        {"slug": LIVE_SIM_OWNER_SLUG},
    ).scalar()


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


def test_rejects_disable_ibkr_link_while_active(active_equal_asset):
    store = active_equal_asset
    link_id = _broker_link_id(store, "live-sim-ibkr-like")
    with pytest.raises(DBAPIError) as exc_info:
        store.session.execute(
            text(
                """
                UPDATE portfolio_broker_accounts
                SET enabled = FALSE
                WHERE id = CAST(:id AS uuid)
                """
            ),
            {"id": link_id},
        )
        store.session.commit()
    store.session.rollback()
    assert "disable broker link" in str(exc_info.value).lower()


def test_rejects_disable_kraken_link_while_active(active_equal_asset):
    store = active_equal_asset
    link_id = _broker_link_id(store, "live-sim-kraken-like")
    with pytest.raises(DBAPIError) as exc_info:
        store.session.execute(
            text(
                """
                UPDATE portfolio_broker_accounts
                SET enabled = FALSE
                WHERE id = CAST(:id AS uuid)
                """
            ),
            {"id": link_id},
        )
        store.session.commit()
    store.session.rollback()
    assert "disable broker link" in str(exc_info.value).lower()


def test_rejects_delete_active_broker_link(active_equal_asset):
    store = active_equal_asset
    link_id = _broker_link_id(store, "live-sim-kraken-like")
    with pytest.raises(DBAPIError) as exc_info:
        store.session.execute(
            text("DELETE FROM portfolio_broker_accounts WHERE id = CAST(:id AS uuid)"),
            {"id": link_id},
        )
        store.session.commit()
    store.session.rollback()
    assert "delete broker link" in str(exc_info.value).lower()


def test_rejects_target_capital_increase_while_active(active_equal_asset):
    store = active_equal_asset
    pid = _portfolio_id(store)
    with pytest.raises(DBAPIError) as exc_info:
        store.session.execute(
            text(
                """
                UPDATE owner_trading_portfolios
                SET target_capital = 12000
                WHERE id = CAST(:pid AS uuid)
                """
            ),
            {"pid": pid},
        )
        store.session.commit()
    store.session.rollback()
    assert "target_capital" in str(exc_info.value).lower()


def test_rejects_target_capital_decrease_while_active(active_equal_asset):
    store = active_equal_asset
    pid = _portfolio_id(store)
    with pytest.raises(DBAPIError) as exc_info:
        store.session.execute(
            text(
                """
                UPDATE owner_trading_portfolios
                SET target_capital = 9000
                WHERE id = CAST(:pid AS uuid)
                """
            ),
            {"pid": pid},
        )
        store.session.commit()
    store.session.rollback()
    assert "target_capital" in str(exc_info.value).lower()


def test_pnl_and_negative_cash_remain_allowed(active_equal_asset):
    store = active_equal_asset
    apply_asset_fill_impact(
        store,
        owner_slug=LIVE_SIM_OWNER_SLUG,
        canonical_symbol="NVDA",
        realized_pnl_delta=Decimal("-1500"),
    )
    store.session.commit()
    cash = store.session.execute(
        text(
            """
            SELECT a.current_cash
            FROM owner_portfolio_asset_allocations a
            JOIN owner_trading_portfolios p ON p.id = a.owner_portfolio_id
            WHERE p.slug = :slug AND a.canonical_symbol = 'NVDA'
            """
        ),
        {"slug": LIVE_SIM_OWNER_SLUG},
    ).scalar()
    assert cash == Decimal("-250")


def test_activation_initializes_vendor_broker_capital(active_equal_asset):
    store = active_equal_asset
    rows = store.session.execute(
        text(
            """
            SELECT ba.slug, ba.starting_cash, ba.cash, ba.equity, ba.is_active,
                   ba.account_state::text, ba.connection_state::text
            FROM broker_accounts ba
            WHERE ba.slug IN ('live-sim-ibkr-like', 'live-sim-kraken-like')
            ORDER BY ba.slug
            """
        )
    ).mappings().all()
    by_slug = {r["slug"]: r for r in rows}
    ibkr = by_slug["live-sim-ibkr-like"]
    kraken = by_slug["live-sim-kraken-like"]
    assert float(ibkr["starting_cash"]) == 7500
    assert float(ibkr["cash"]) == 7500
    assert float(ibkr["equity"]) == 7500
    assert ibkr["is_active"] is True
    assert ibkr["account_state"] == "active"
    assert ibkr["connection_state"] == "CONNECTED"
    assert float(kraken["starting_cash"]) == 2500
    assert float(kraken["cash"]) == 2500
    assert float(kraken["equity"]) == 2500
    assert kraken["is_active"] is True
    assert kraken["connection_state"] == "CONNECTED"
