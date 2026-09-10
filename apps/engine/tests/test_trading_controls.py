"""Trading control state machine."""

from quantara_engine.trading.trading_controls import (
    TradingControlSnapshot,
    TradingControlState,
    allows_new_entries,
    allows_strategy_evaluation,
    load_trading_control,
)


def test_running_allows_all():
    snap = TradingControlSnapshot(state=TradingControlState.RUNNING)
    assert allows_new_entries(snap)
    assert allows_strategy_evaluation(snap)


def test_pause_new_entries_blocks_entries_only():
    snap = TradingControlSnapshot(state=TradingControlState.PAUSE_NEW_ENTRIES)
    assert not allows_new_entries(snap)
    assert allows_strategy_evaluation(snap)


def test_pause_trading_blocks_strategy_and_entries():
    snap = TradingControlSnapshot(state=TradingControlState.PAUSE_TRADING)
    assert not allows_new_entries(snap)
    assert not allows_strategy_evaluation(snap)


def test_load_from_settings_defaults_running():
    snap = load_trading_control({})
    assert snap.state == TradingControlState.RUNNING
