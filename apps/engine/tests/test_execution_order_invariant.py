"""Strategy runner must not execute pending intents — delegated to execute_intents job."""

from unittest.mock import MagicMock, patch

from quantara_engine.pipeline.candle_processor import CandleProcessor


def test_process_candle_does_not_execute_pending_by_default():
    state = MagicMock()
    state.portfolio = MagicMock()
    state.open_positions.return_value = []
    processor = CandleProcessor(
        portfolio_state=state,
        strategy_instance=MagicMock(id="si1", strategy_version_id="v1", strategy_slug="gold-trend-pullback"),
        instrument=MagicMock(id="i1", symbol="BTCUSD"),
        risk_profile=MagicMock(),
        broker=MagicMock(),
        store=None,
        allow_live_execution=True,
        execute_pending_in_process=False,
    )
    candle = MagicMock(
        instrument_id="i1",
        timeframe="5m",
        timestamp=MagicMock(),
        open=1,
        high=1,
        low=1,
        close=1,
    )
    processor.all_candles = [candle]
    processor._execute_pending = MagicMock()
    processor.evaluate_signal = MagicMock(return_value=(MagicMock(action=MagicMock(value="hold"), reason="hold"), candle))
    processor._persist_signal = MagicMock()
    processor._handle_signal_decisions = MagicMock()
    processor.process_candle(0)
    processor._execute_pending.assert_not_called()
