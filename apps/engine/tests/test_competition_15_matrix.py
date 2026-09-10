"""Focused tests for multi-asset multi-timeframe competition constants."""

from quantara_engine.competition.constants import (
    ACTIVE_COMPETITION_EXPERIMENT_ID,
    ACTIVE_COMPETITION_PORTFOLIOS,
    ARCHIVED_XAU_COMPETITION_EXPERIMENT_ID,
    COMPETITION_INITIAL_CAPITAL,
    COMPETITION_TOTAL_INITIAL,
    LEGACY_COMPETITION_EXPERIMENT_ID,
    LEGACY_COMPETITION_PORTFOLIOS,
    RISK_TIERS,
    TIMEFRAME_ORDER,
)
from quantara_engine.market_data.active_universe import list_active_db_symbols


def test_120_portfolios_matrix():
    assert len(ACTIVE_COMPETITION_PORTFOLIOS) == 120
    assert COMPETITION_TOTAL_INITIAL == 120 * COMPETITION_INITIAL_CAPITAL

    for symbol in list_active_db_symbols():
        for timeframe in TIMEFRAME_ORDER:
            group = [
                p
                for p in ACTIVE_COMPETITION_PORTFOLIOS
                if p.symbol == symbol and p.timeframe == timeframe
            ]
            assert len(group) == 5
            slugs = {p.risk_slug for p in group}
            assert slugs == {slug for slug, _ in RISK_TIERS}


def test_legacy_experiments_preserved():
    assert len(LEGACY_COMPETITION_PORTFOLIOS) == 5
    assert LEGACY_COMPETITION_EXPERIMENT_ID != ACTIVE_COMPETITION_EXPERIMENT_ID
    assert ARCHIVED_XAU_COMPETITION_EXPERIMENT_ID != ACTIVE_COMPETITION_EXPERIMENT_ID


def test_unique_portfolio_and_instance_ids():
    portfolio_ids = [p.portfolio_id for p in ACTIVE_COMPETITION_PORTFOLIOS]
    instance_ids = [p.instance_id for p in ACTIVE_COMPETITION_PORTFOLIOS]
    assert len(portfolio_ids) == len(set(portfolio_ids))
    assert len(instance_ids) == len(set(instance_ids))


def test_each_instance_bound_to_correct_asset():
    for entry in ACTIVE_COMPETITION_PORTFOLIOS:
        assert entry.symbol in list_active_db_symbols()
