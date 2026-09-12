"""ORB competition activation — 120 + 40 portfolios, idempotent enable."""

from __future__ import annotations

from unittest.mock import MagicMock

from quantara_engine.competition.constants import (
    ACTIVE_COMPETITION_PORTFOLIOS,
    COMPETITION_INITIAL_CAPITAL,
)
from quantara_engine.competition.orb_constants import (
    ORB_COMPETITION_INITIAL_CAPITAL,
    ORB_COMPETITION_PORTFOLIOS,
    ORB_SETTINGS_ENABLED_KEY,
)
from quantara_engine.persistence.store import TradingStore


def test_portfolio_constants_sum_to_160():
    assert len(ACTIVE_COMPETITION_PORTFOLIOS) == 120
    assert len(ORB_COMPETITION_PORTFOLIOS) == 40
    robot_a_capital = len(ACTIVE_COMPETITION_PORTFOLIOS) * float(COMPETITION_INITIAL_CAPITAL)
    robot_b_capital = len(ORB_COMPETITION_PORTFOLIOS) * float(ORB_COMPETITION_INITIAL_CAPITAL)
    assert robot_a_capital == 240_000
    assert robot_b_capital == 80_000
    assert robot_a_capital + robot_b_capital == 320_000


def test_list_all_competition_entries_returns_160_when_orb_enabled():
    store = MagicMock(spec=TradingStore)
    store.is_orb_competition_enabled.return_value = True
    store.get_settings_dict.return_value = {ORB_SETTINGS_ENABLED_KEY: True}
    robot_a = [object()] * len(ACTIVE_COMPETITION_PORTFOLIOS)
    robot_b = [object()] * len(ORB_COMPETITION_PORTFOLIOS)
    store.list_all_competition_entries.return_value = (robot_a, robot_b, robot_a + robot_b)

    a, b, combined = store.list_all_competition_entries()
    assert len(a) == 120
    assert len(b) == 40
    assert len(combined) == 160


def test_list_all_competition_entries_returns_120_when_orb_disabled():
    store = MagicMock(spec=TradingStore)
    store.is_orb_competition_enabled.return_value = False
    robot_a = [object()] * len(ACTIVE_COMPETITION_PORTFOLIOS)
    store.list_all_competition_entries.return_value = (robot_a, [], robot_a)

    a, b, combined = store.list_all_competition_entries()
    assert len(a) == 120
    assert len(b) == 0
    assert len(combined) == 120
