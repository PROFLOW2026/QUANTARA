"""ORB financial isolation — Robot A untouched, ORB paper disabled by default."""

from __future__ import annotations

from unittest.mock import MagicMock

from quantara_engine.competition.orb_constants import (
    ORB_COMPETITION_EXPERIMENT_ID,
    ORB_SETTINGS_ENABLED_KEY,
    ORB_STRATEGY_SLUG,
)
from quantara_engine.persistence.store import TradingStore
from quantara_engine.strategies.gold_trend_pullback.v1_0_0 import GoldTrendPullbackV1
from quantara_engine.strategies.registry import STRATEGY_REGISTRY


def test_orb_competition_disabled_by_default():
    store = TradingStore(MagicMock())
    store.get_settings_dict = MagicMock(return_value={ORB_SETTINGS_ENABLED_KEY: False})
    assert store.is_orb_competition_enabled() is False
    assert store.list_orb_competition_entries() == []


def test_robot_a_registry_unchanged():
    assert "gold-trend-pullback" in STRATEGY_REGISTRY
    assert "opening-range-breakout" in STRATEGY_REGISTRY
    gtp = GoldTrendPullbackV1()
    assert gtp.strategy_id() == "gold-trend-pullback"
    assert gtp.version() == "1.0.0"


def test_orb_experiment_isolated_from_robot_a():
    from quantara_engine.competition.constants import ACTIVE_COMPETITION_EXPERIMENT_ID

    assert ORB_COMPETITION_EXPERIMENT_ID != ACTIVE_COMPETITION_EXPERIMENT_ID
    assert ORB_STRATEGY_SLUG != "gold-trend-pullback"
