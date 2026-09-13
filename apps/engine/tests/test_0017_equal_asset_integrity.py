"""0017 equal-asset integrity — post-activation invariants, referential guards, loss semantics."""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from quantara_engine.owner_portfolio.asset_allocation import (
    configure_equal_asset_allocations,
    deactivate_equal_asset_allocations,
    is_equal_asset_mode_active,
    list_asset_allocations,
)
from quantara_engine.owner_portfolio.asset_ledger import apply_asset_fill_impact
from quantara_engine.owner_portfolio.constants import (
    LIVE_SIM_IBKR_TOTAL,
    LIVE_SIM_KRAKEN_TOTAL,
    LIVE_SIM_PER_ASSET_CAPITAL,
)
from quantara_engine.owner_portfolio.service import LIVE_SIM_OWNER_SLUG


def _asset_id(store, symbol: str) -> str:
    return store.session.execute(
        text(
            """
            SELECT a.id::text
            FROM owner_portfolio_asset_allocations a
            JOIN owner_trading_portfolios otp ON otp.id = a.owner_portfolio_id
            WHERE otp.slug = :slug AND a.canonical_symbol = :sym
            """
        ),
        {"slug": LIVE_SIM_OWNER_SLUG, "sym": symbol},
    ).scalar()


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


def test_active_mode_has_eight_equal_assets(active_equal_asset):
    store = active_equal_asset
    assets = list_asset_allocations(store, LIVE_SIM_OWNER_SLUG)
    assert len(assets) == 8
    assert all(a.enabled for a in assets)
    assert all(a.starting_allocated_capital == LIVE_SIM_PER_ASSET_CAPITAL for a in assets)
    assert sum(a.starting_allocated_capital for a in assets) == Decimal("10000")


def test_rejects_nvda_starting_reduction_while_active(active_equal_asset):
    store = active_equal_asset
    aid = _asset_id(store, "NVDA")
    with pytest.raises(DBAPIError) as exc_info:
        store.session.execute(
            text(
                """
                UPDATE owner_portfolio_asset_allocations
                SET starting_allocated_capital = 1000
                WHERE id = CAST(:id AS uuid)
                """
            ),
            {"id": aid},
        )
        store.session.commit()
    store.session.rollback()
    assert "starting_allocated_capital" in str(exc_info.value).lower()


def test_rejects_btc_eth_unequal_while_active(active_equal_asset):
    store = active_equal_asset
    btc_id = _asset_id(store, "BTCUSD")
    with pytest.raises(DBAPIError):
        store.session.execute(
            text(
                """
                UPDATE owner_portfolio_asset_allocations
                SET starting_allocated_capital = 1500
                WHERE id = CAST(:id AS uuid)
                """
            ),
            {"id": btc_id},
        )
        store.session.commit()
    store.session.rollback()

    eth_id = _asset_id(store, "ETHUSD")
    with pytest.raises(DBAPIError):
        store.session.execute(
            text(
                """
                UPDATE owner_portfolio_asset_allocations
                SET starting_allocated_capital = 1000
                WHERE id = CAST(:id AS uuid)
                """
            ),
            {"id": eth_id},
        )
        store.session.commit()
    store.session.rollback()


def test_rejects_disable_asset_while_active(active_equal_asset):
    store = active_equal_asset
    aid = _asset_id(store, "AMD")
    with pytest.raises(DBAPIError) as exc_info:
        store.session.execute(
            text(
                """
                UPDATE owner_portfolio_asset_allocations
                SET enabled = FALSE
                WHERE id = CAST(:id AS uuid)
                """
            ),
            {"id": aid},
        )
        store.session.commit()
    store.session.rollback()
    assert "disable" in str(exc_info.value).lower()


def test_rejects_delete_asset_while_active(active_equal_asset):
    store = active_equal_asset
    aid = _asset_id(store, "COIN")
    with pytest.raises(DBAPIError) as exc_info:
        store.session.execute(
            text(
                """
                DELETE FROM owner_portfolio_asset_allocations
                WHERE id = CAST(:id AS uuid)
                """
            ),
            {"id": aid},
        )
        store.session.commit()
    store.session.rollback()
    assert "delete" in str(exc_info.value).lower()


def test_rejects_mismatched_broker_link(active_equal_asset):
    store = active_equal_asset
    ibkr_link = _broker_link_id(store, "live-sim-ibkr-like")
    btc_id = _asset_id(store, "BTCUSD")
    with pytest.raises(DBAPIError) as exc_info:
        store.session.execute(
            text(
                """
                UPDATE owner_portfolio_asset_allocations
                SET portfolio_broker_account_id = CAST(:link AS uuid)
                WHERE id = CAST(:id AS uuid)
                """
            ),
            {"id": btc_id, "link": ibkr_link},
        )
        store.session.commit()
    store.session.rollback()
    msg = str(exc_info.value).lower()
    assert "routing" in msg or "vendor" in msg or "broker" in msg


def test_rejects_cross_owner_broker_link(active_equal_asset):
    store = active_equal_asset
    foreign_owner = store.session.execute(
        text(
            """
            INSERT INTO owner_trading_portfolios (slug, name, target_capital)
            VALUES (:slug, 'Foreign Owner', 5000)
            ON CONFLICT (slug) DO UPDATE SET updated_at = NOW()
            RETURNING id::text
            """
        ),
        {"slug": "__test_foreign_owner__"},
    ).scalar()
    foreign_ba = store.session.execute(
        text(
            """
            INSERT INTO broker_accounts (
              slug, profile_slug, position_mode, starting_cash, cash, balance, equity,
              spot_crypto_cash, is_active, pending_owner_reset, account_state,
              broker_vendor, broker_environment, connection_state
            ) VALUES (
              :slug, 'quantara_live_sim_10k', 'netting',
              2500, 2500, 2500, 2500, 0, FALSE, FALSE, 'paused',
              'KRAKEN', 'SIMULATION', 'DISCONNECTED'
            )
            ON CONFLICT (slug) DO UPDATE SET updated_at = NOW()
            RETURNING id::text
            """
        ),
        {"slug": "__test_foreign_kraken__"},
    ).scalar()
    foreign_link = store.session.execute(
        text(
            """
            INSERT INTO portfolio_broker_accounts (
              owner_portfolio_id, broker_account_id, allocated_capital, enabled, is_legacy_primary
            ) VALUES (
              CAST(:pid AS uuid), CAST(:aid AS uuid), 2500, FALSE, FALSE
            )
            RETURNING id::text
            """
        ),
        {"pid": foreign_owner, "aid": foreign_ba},
    ).scalar()
    store.session.commit()
    nvda_id = _asset_id(store, "NVDA")
    with pytest.raises(DBAPIError) as exc_info:
        store.session.execute(
            text(
                """
                UPDATE owner_portfolio_asset_allocations
                SET portfolio_broker_account_id = CAST(:link AS uuid)
                WHERE id = CAST(:id AS uuid)
                """
            ),
            {"id": nvda_id, "link": foreign_link},
        )
        store.session.commit()
    store.session.rollback()
    msg = str(exc_info.value).lower()
    assert "different owner" in msg or "broker routing" in msg


def test_rejects_broker_aggregate_drift(active_equal_asset):
    store = active_equal_asset
    ibkr_link = _broker_link_id(store, "live-sim-ibkr-like")
    with pytest.raises(DBAPIError) as exc_info:
        store.session.execute(
            text(
                """
                UPDATE portfolio_broker_accounts
                SET allocated_capital = 8000
                WHERE id = CAST(:id AS uuid)
                """
            ),
            {"id": ibkr_link},
        )
        store.session.commit()
    store.session.rollback()
    assert "allocated_capital" in str(exc_info.value).lower()


def test_broker_totals_match_asset_sums(active_equal_asset):
    store = active_equal_asset
    rows = store.session.execute(
        text(
            """
            SELECT ba.slug, pba.allocated_capital,
                   COALESCE(SUM(a.starting_allocated_capital), 0) AS asset_sum
            FROM portfolio_broker_accounts pba
            JOIN broker_accounts ba ON ba.id = pba.broker_account_id
            JOIN owner_trading_portfolios otp ON otp.id = pba.owner_portfolio_id
            LEFT JOIN owner_portfolio_asset_allocations a
              ON a.portfolio_broker_account_id = pba.id AND a.enabled = TRUE
            WHERE otp.slug = :slug AND NOT pba.is_legacy_primary AND pba.enabled = TRUE
            GROUP BY ba.slug, pba.allocated_capital
            ORDER BY ba.slug
            """
        ),
        {"slug": LIVE_SIM_OWNER_SLUG},
    ).mappings().all()
    by_slug = {r["slug"]: r for r in rows}
    assert float(by_slug["live-sim-ibkr-like"]["allocated_capital"]) == float(LIVE_SIM_IBKR_TOTAL)
    assert float(by_slug["live-sim-kraken-like"]["allocated_capital"]) == float(LIVE_SIM_KRAKEN_TOTAL)
    assert float(by_slug["live-sim-ibkr-like"]["asset_sum"]) == float(LIVE_SIM_IBKR_TOTAL)
    assert float(by_slug["live-sim-kraken-like"]["asset_sum"]) == float(LIVE_SIM_KRAKEN_TOTAL)


def test_negative_loss_accounting_truthful(active_equal_asset):
    store = active_equal_asset
    apply_asset_fill_impact(
        store,
        owner_slug=LIVE_SIM_OWNER_SLUG,
        canonical_symbol="BTCUSD",
        realized_pnl_delta=Decimal("-2000"),
    )
    store.session.commit()
    btc = next(
        a for a in list_asset_allocations(store, LIVE_SIM_OWNER_SLUG) if a.canonical_symbol == "BTCUSD"
    )
    assert btc.current_cash == Decimal("-750")
    assert btc.current_equity == Decimal("-750")
    assert btc.realized_pnl == Decimal("-2000")


def test_flag_consistency_rejects_multi_broker_without_equal(active_equal_asset):
    store = active_equal_asset
    pid = store.session.execute(
        text("SELECT id::text FROM owner_trading_portfolios WHERE slug = :slug"),
        {"slug": LIVE_SIM_OWNER_SLUG},
    ).scalar()
    with pytest.raises(DBAPIError):
        store.session.execute(
            text(
                """
                UPDATE owner_trading_portfolios
                SET equal_asset_allocation_enabled = FALSE
                WHERE id = CAST(:pid AS uuid)
                """
            ),
            {"pid": pid},
        )
        store.session.commit()
    store.session.rollback()


def test_deactivate_returns_to_off(active_equal_asset):
    store = active_equal_asset
    assert is_equal_asset_mode_active(store, LIVE_SIM_OWNER_SLUG)
    result = deactivate_equal_asset_allocations(store, LIVE_SIM_OWNER_SLUG)
    assert result["ok"]
    assert is_equal_asset_mode_active(store, LIVE_SIM_OWNER_SLUG) is False
    row = store.session.execute(
        text(
            """
            SELECT multi_broker_mode_enabled, equal_asset_allocation_enabled
            FROM owner_trading_portfolios WHERE slug = :slug
            """
        ),
        {"slug": LIVE_SIM_OWNER_SLUG},
    ).mappings().first()
    assert row["multi_broker_mode_enabled"] is False
    assert row["equal_asset_allocation_enabled"] is False
