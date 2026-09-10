"""Robot A explicit tradable universe."""

from __future__ import annotations

from quantara_engine.competition.robot_a_universe import (
    list_robot_a_tradable_db_symbols,
)
from quantara_engine.market_data.active_universe import ACTIVE_DB_SYMBOLS
from quantara_workers.jobs.run_strategy import _robot_a_symbols


def test_robot_a_universe_matches_active_eight():
    assert list_robot_a_tradable_db_symbols() == ACTIVE_DB_SYMBOLS
    assert _robot_a_symbols() == list(ACTIVE_DB_SYMBOLS)


def test_removed_symbols_not_tradable():
    for sym in ("EURUSD", "SPY", "QQQ", "AAPL", "MSFT"):
        assert sym not in list_robot_a_tradable_db_symbols()
