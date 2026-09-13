"""Equal-asset Live Sim capital allocation — 8 × $1,250 envelopes."""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from quantara_engine.broker.live_execution_gate import REAL_BROKER_SUBMISSION_ENABLED
from quantara_engine.competition.paper_run import get_current_paper_run_id
from quantara_engine.owner_portfolio.asset_allocation import (
    configure_equal_asset_allocations,
    derive_broker_totals_from_assets,
    is_equal_asset_configured,
    is_equal_asset_mode_active,
    list_asset_allocations,
    validate_equal_asset_allocations,
)
from quantara_engine.owner_portfolio.asset_ledger import (
    aggregate_assets_by_broker,
    aggregate_owner_from_assets,
    apply_asset_fill_impact,
)
from quantara_engine.owner_portfolio.asset_risk import evaluate_asset_envelope_risk
from quantara_engine.owner_portfolio.constants import (
    LIVE_SIM_IBKR_TOTAL,
    LIVE_SIM_KRAKEN_TOTAL,
    LIVE_SIM_PER_ASSET_CAPITAL,
    LIVE_SIM_TARGET_CAPITAL,
)
from quantara_engine.owner_portfolio.global_risk import evaluate_owner_global_risk
from quantara_engine.owner_portfolio.service import LIVE_SIM_OWNER_SLUG


RESEARCH_RUN_ID = "231d0c57-e991-4008-914c-aac92ab2bf2c"


def _configure_draft(broker_test_store) -> dict:
    return configure_equal_asset_allocations(
        broker_test_store, LIVE_SIM_OWNER_SLUG, activate=False
    )


def _enable_equal_asset_mode(broker_test_store) -> None:
    if is_equal_asset_mode_active(broker_test_store, LIVE_SIM_OWNER_SLUG):
        return
    result = configure_equal_asset_allocations(
        broker_test_store, LIVE_SIM_OWNER_SLUG, activate=True
    )
    assert result["ok"], result
    assert is_equal_asset_mode_active(broker_test_store, LIVE_SIM_OWNER_SLUG)


def test_eight_assets_times_1250_equals_10k():
    v = validate_equal_asset_allocations()
    assert v.valid
    assert v.asset_sum == LIVE_SIM_TARGET_CAPITAL
    assert v.ibkr_sum == LIVE_SIM_IBKR_TOTAL
    assert v.kraken_sum == LIVE_SIM_KRAKEN_TOTAL


def test_ibkr_7500_kraken_2500(broker_test_store):
    result = _configure_draft(broker_test_store)
    assert result["ok"]
    assert result["ibkr_total"] == 7500.0
    assert result["kraken_total"] == 2500.0
    assets = list_asset_allocations(broker_test_store, LIVE_SIM_OWNER_SLUG)
    assert len(assets) == 8
    assert sum(float(a.starting_allocated_capital) for a in assets) == 10000.0


def test_asset_allocation_persisted(broker_test_store):
    _configure_draft(broker_test_store)
    assert is_equal_asset_configured(broker_test_store, LIVE_SIM_OWNER_SLUG)
    row = broker_test_store.session.execute(
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


def test_multi_broker_remains_off_after_draft_configure(broker_test_store):
    _configure_draft(broker_test_store)
    assert is_equal_asset_mode_active(broker_test_store, LIVE_SIM_OWNER_SLUG) is False


def test_asset_risk_inactive_while_multi_broker_off(broker_test_store):
    _configure_draft(broker_test_store)
    verdict = evaluate_asset_envelope_risk(
        broker_test_store,
        owner_slug=LIVE_SIM_OWNER_SLUG,
        canonical_symbol="ETHUSD",
        incremental_sl_risk_usd=Decimal("2000"),
    )
    assert verdict.allowed is True
    assert verdict.reason == "equal_asset_mode_inactive"


def test_broker_derived_from_assets(broker_test_store):
    if is_equal_asset_mode_active(broker_test_store, LIVE_SIM_OWNER_SLUG):
        totals = derive_broker_totals_from_assets(broker_test_store, LIVE_SIM_OWNER_SLUG)
        assert totals["IBKR"] == LIVE_SIM_IBKR_TOTAL
        assert totals["KRAKEN"] == LIVE_SIM_KRAKEN_TOTAL
        return
    _enable_equal_asset_mode(broker_test_store)
    totals = derive_broker_totals_from_assets(broker_test_store, LIVE_SIM_OWNER_SLUG)
    assert totals["IBKR"] == LIVE_SIM_IBKR_TOTAL
    assert totals["KRAKEN"] == LIVE_SIM_KRAKEN_TOTAL


def test_asset_cannot_spend_other_asset_capital(broker_test_store):
    _enable_equal_asset_mode(broker_test_store)
    btc_before = next(
        a for a in list_asset_allocations(broker_test_store, LIVE_SIM_OWNER_SLUG) if a.canonical_symbol == "BTCUSD"
    )
    verdict = evaluate_asset_envelope_risk(
        broker_test_store,
        owner_slug=LIVE_SIM_OWNER_SLUG,
        canonical_symbol="BTCUSD",
        incremental_sl_risk_usd=Decimal("50"),
        required_cash_usd=btc_before.current_cash + Decimal("100"),
    )
    assert verdict.allowed is False
    assert verdict.reason == "asset_cash_insufficient"


def test_btc_profit_changes_btc_equity_only(broker_test_store):
    _enable_equal_asset_mode(broker_test_store)
    apply_asset_fill_impact(
        broker_test_store,
        owner_slug=LIVE_SIM_OWNER_SLUG,
        canonical_symbol="BTCUSD",
        realized_pnl_delta=Decimal("100"),
    )
    broker_test_store.session.commit()
    btc = next(
        a for a in list_asset_allocations(broker_test_store, LIVE_SIM_OWNER_SLUG) if a.canonical_symbol == "BTCUSD"
    )
    nvda = next(
        a for a in list_asset_allocations(broker_test_store, LIVE_SIM_OWNER_SLUG) if a.canonical_symbol == "NVDA"
    )
    assert btc.current_equity == Decimal("1350")
    assert nvda.current_equity == Decimal("1250")


def test_nvda_loss_changes_nvda_equity_only(broker_test_store):
    _enable_equal_asset_mode(broker_test_store)
    btc_before = next(
        a for a in list_asset_allocations(broker_test_store, LIVE_SIM_OWNER_SLUG) if a.canonical_symbol == "BTCUSD"
    ).current_equity
    apply_asset_fill_impact(
        broker_test_store,
        owner_slug=LIVE_SIM_OWNER_SLUG,
        canonical_symbol="NVDA",
        realized_pnl_delta=Decimal("-50"),
    )
    broker_test_store.session.commit()
    nvda = next(
        a for a in list_asset_allocations(broker_test_store, LIVE_SIM_OWNER_SLUG) if a.canonical_symbol == "NVDA"
    )
    btc = next(
        a for a in list_asset_allocations(broker_test_store, LIVE_SIM_OWNER_SLUG) if a.canonical_symbol == "BTCUSD"
    )
    assert nvda.current_equity == Decimal("1200")
    assert btc.current_equity == btc_before


def test_broker_aggregate_equals_asset_aggregates(broker_test_store):
    _enable_equal_asset_mode(broker_test_store)
    before = aggregate_assets_by_broker(broker_test_store, LIVE_SIM_OWNER_SLUG)
    apply_asset_fill_impact(
        broker_test_store,
        owner_slug=LIVE_SIM_OWNER_SLUG,
        canonical_symbol="BTCUSD",
        realized_pnl_delta=Decimal("100"),
    )
    apply_asset_fill_impact(
        broker_test_store,
        owner_slug=LIVE_SIM_OWNER_SLUG,
        canonical_symbol="ETHUSD",
        realized_pnl_delta=Decimal("-20"),
    )
    broker_test_store.session.commit()
    by_broker = aggregate_assets_by_broker(broker_test_store, LIVE_SIM_OWNER_SLUG)
    assert by_broker["KRAKEN"]["equity"] == before["KRAKEN"]["equity"] + Decimal("80")
    assert by_broker["IBKR"]["equity"] == before["IBKR"]["equity"]


def test_owner_aggregate_equals_all_assets_once(broker_test_store):
    _enable_equal_asset_mode(broker_test_store)
    before = aggregate_owner_from_assets(broker_test_store, LIVE_SIM_OWNER_SLUG)
    apply_asset_fill_impact(
        broker_test_store,
        owner_slug=LIVE_SIM_OWNER_SLUG,
        canonical_symbol="NVDA",
        realized_pnl_delta=Decimal("40"),
    )
    broker_test_store.session.commit()
    owner = aggregate_owner_from_assets(broker_test_store, LIVE_SIM_OWNER_SLUG)
    assets = list_asset_allocations(broker_test_store, LIVE_SIM_OWNER_SLUG)
    asset_sum = sum(a.current_equity for a in assets if a.enabled)
    assert owner["equity"] == asset_sum
    assert owner["equity"] == before["equity"] + Decimal("40")


def test_fees_attributed_to_correct_asset(broker_test_store):
    _enable_equal_asset_mode(broker_test_store)
    apply_asset_fill_impact(
        broker_test_store,
        owner_slug=LIVE_SIM_OWNER_SLUG,
        canonical_symbol="TSLA",
        fee_delta=Decimal("2.50"),
    )
    broker_test_store.session.commit()
    tsla = next(
        a for a in list_asset_allocations(broker_test_store, LIVE_SIM_OWNER_SLUG) if a.canonical_symbol == "TSLA"
    )
    amd = next(
        a for a in list_asset_allocations(broker_test_store, LIVE_SIM_OWNER_SLUG) if a.canonical_symbol == "AMD"
    )
    assert tsla.fees_paid == Decimal("2.50")
    assert tsla.current_cash == Decimal("1247.50")
    assert amd.fees_paid == Decimal("0")


def test_funding_attributed_to_correct_asset(broker_test_store):
    _enable_equal_asset_mode(broker_test_store)
    btc_before = next(
        a for a in list_asset_allocations(broker_test_store, LIVE_SIM_OWNER_SLUG) if a.canonical_symbol == "BTCUSD"
    )
    apply_asset_fill_impact(
        broker_test_store,
        owner_slug=LIVE_SIM_OWNER_SLUG,
        canonical_symbol="BTCUSD",
        funding_delta=Decimal("1.25"),
    )
    broker_test_store.session.commit()
    btc = next(
        a for a in list_asset_allocations(broker_test_store, LIVE_SIM_OWNER_SLUG) if a.canonical_symbol == "BTCUSD"
    )
    assert btc.funding_paid == btc_before.funding_paid + Decimal("1.25")
    assert btc.current_cash == btc_before.current_cash - Decimal("1.25")


def test_global_risk_applies_when_equal_asset_active(broker_test_store):
    _enable_equal_asset_mode(broker_test_store)
    verdict = evaluate_owner_global_risk(
        broker_test_store,
        owner_slug=LIVE_SIM_OWNER_SLUG,
        symbol="BTCUSD",
        incremental_sl_risk_usd=Decimal("500"),
    )
    assert verdict.allowed is False
    assert verdict.reason == "owner_max_total_sl_risk"


def test_asset_risk_independently_rejects(broker_test_store):
    _enable_equal_asset_mode(broker_test_store)
    verdict = evaluate_asset_envelope_risk(
        broker_test_store,
        owner_slug=LIVE_SIM_OWNER_SLUG,
        canonical_symbol="ETHUSD",
        incremental_sl_risk_usd=Decimal("2000"),
    )
    assert verdict.allowed is False


def test_research_unchanged(broker_test_store):
    paper = broker_test_store.session.execute(
        text("SELECT slug FROM broker_accounts WHERE slug = 'quantara_paper_competition'")
    ).scalar()
    assert paper == "quantara_paper_competition"
    count = broker_test_store.session.execute(text("SELECT COUNT(*) FROM portfolios")).scalar()
    assert int(count) >= 160


def test_live_sim_legacy_preserved(broker_test_store):
    row = broker_test_store.session.execute(
        text("SELECT slug FROM broker_accounts WHERE slug = 'live-sim-10k'")
    ).scalar()
    assert row == "live-sim-10k"


def test_real_submission_still_disabled():
    assert REAL_BROKER_SUBMISSION_ENABLED is False


def test_db_rejects_asset_over_target(broker_test_store):
    pid = broker_test_store.session.execute(
        text("SELECT id::text FROM owner_trading_portfolios WHERE slug = :slug"),
        {"slug": LIVE_SIM_OWNER_SLUG},
    ).scalar()
    with pytest.raises(DBAPIError):
        broker_test_store.session.execute(
            text(
                """
                INSERT INTO owner_portfolio_asset_allocations (
                  owner_portfolio_id, canonical_symbol, starting_allocated_capital,
                  current_cash, enabled, label_he
                ) VALUES (
                  CAST(:pid AS uuid), 'TESTOVER', 11000, 11000, TRUE, 'test'
                )
                """
            ),
            {"pid": pid},
        )
        broker_test_store.session.commit()
    broker_test_store.session.rollback()
