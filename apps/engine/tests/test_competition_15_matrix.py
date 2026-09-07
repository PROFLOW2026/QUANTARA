"""Focused tests for multi-timeframe competition constants."""

from quantara_engine.competition.constants import (
    ACTIVE_COMPETITION_EXPERIMENT_ID,
    ACTIVE_COMPETITION_PORTFOLIOS,
    COMPETITION_INITIAL_CAPITAL,
    COMPETITION_TOTAL_INITIAL,
    LEGACY_COMPETITION_EXPERIMENT_ID,
    LEGACY_COMPETITION_PORTFOLIOS,
    RISK_TIERS,
    TIMEFRAME_ORDER,
)


def test_fifteen_portfolios_matrix():
    assert len(ACTIVE_COMPETITION_PORTFOLIOS) == 15
    assert COMPETITION_TOTAL_INITIAL == 15 * COMPETITION_INITIAL_CAPITAL

    for timeframe in TIMEFRAME_ORDER:
        group = [p for p in ACTIVE_COMPETITION_PORTFOLIOS if p.timeframe == timeframe]
        assert len(group) == 5
        slugs = {p.risk_slug for p in group}
        assert slugs == {slug for slug, _ in RISK_TIERS}


def test_legacy_experiment_preserved():
    assert len(LEGACY_COMPETITION_PORTFOLIOS) == 5
    assert LEGACY_COMPETITION_EXPERIMENT_ID != ACTIVE_COMPETITION_EXPERIMENT_ID


def test_unique_portfolio_and_instance_ids():
    portfolio_ids = [p.portfolio_id for p in ACTIVE_COMPETITION_PORTFOLIOS]
    instance_ids = [p.instance_id for p in ACTIVE_COMPETITION_PORTFOLIOS]
    assert len(portfolio_ids) == len(set(portfolio_ids))
    assert len(instance_ids) == len(set(instance_ids))
