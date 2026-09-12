"""Manual close and per-asset trading pause."""

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from quantara_engine.domain.types import (
    Candle,
    Direction,
    Mode,
    Portfolio,
    Position,
    PositionStatus,
)
from quantara_engine.execution.manual_close import (
    MANUAL_CLOSE_IDEMPOTENCY_PREFIX,
    close_research_position,
)
from quantara_engine.trading.asset_trading_controls import (
    SCOPE_RESEARCH,
    allows_entries_for_symbol,
    load_asset_trading_controls,
    set_asset_trading_paused,
)


def test_asset_pause_blocks_entries_not_exits():
    settings = {
        "asset_trading_controls": {
            "research": {"ETHUSD": True},
            "live_sim": {},
        }
    }
    assert not allows_entries_for_symbol(settings, scope=SCOPE_RESEARCH, symbol="ETH/USD")
    assert allows_entries_for_symbol(settings, scope=SCOPE_RESEARCH, symbol="BTCUSD")


def test_set_asset_trading_paused_persists():
    store = MagicMock()
    saved: dict = {}

    def _update(key, value):
        saved[key] = value

    store.get_settings_dict.return_value = {}
    store.update_settings.side_effect = _update

    snap = set_asset_trading_paused(store, scope=SCOPE_RESEARCH, symbol="ETH/USD", paused=True)
    assert snap.is_paused(SCOPE_RESEARCH, "ETHUSD")
    assert saved["asset_trading_controls"]["research"]["ETHUSD"] is True

    snap = set_asset_trading_paused(store, scope=SCOPE_RESEARCH, symbol="ETH/USD", paused=False)
    assert not snap.is_paused(SCOPE_RESEARCH, "ETHUSD")


def test_manual_close_idempotency_key_stable():
    assert MANUAL_CLOSE_IDEMPOTENCY_PREFIX + "pos-1" == "manual:close:pos-1"


def _open_position(*, position_id: str = "pos-1") -> Position:
    return Position(
        id=position_id,
        portfolio_id="pf-1",
        strategy_instance_id="si-1",
        instrument_id="inst-eth",
        direction=Direction.LONG,
        quantity=Decimal("0.5"),
        entry_price=Decimal("3000"),
        stop_loss=Decimal("2900"),
        take_profit=Decimal("3200"),
        current_price=Decimal("3050"),
        status=PositionStatus.OPEN,
        opened_at=datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc),
    )


def test_close_research_position_not_found():
    store = MagicMock()
    store.get_open_position_by_id.return_value = None
    store.trade_exists_for_position.return_value = False
    result = close_research_position(store, "missing")
    assert result.status == "not_found"


def test_close_research_position_already_closed():
    store = MagicMock()
    store.get_open_position_by_id.return_value = None
    store.trade_exists_for_position.return_value = True
    result = close_research_position(store, "pos-closed")
    assert result.status == "already_closed"


def test_close_research_position_market_closed():
    store = MagicMock()
    store.get_open_position_by_id.return_value = _open_position()
    store.get_instrument_by_id.return_value = MagicMock(id="inst-eth", symbol="ETHUSD")
    store.get_strategy_instance_by_id.return_value = MagicMock(id="si-1", timeframe="5m")

    with patch("quantara_engine.execution.manual_close._asset_tradable", return_value=False):
        result = close_research_position(
            store,
            "pos-1",
            now=datetime(2026, 9, 13, 3, 0, tzinfo=timezone.utc),
        )
    assert result.status == "pending_market"


def test_candle_processor_blocks_entry_when_asset_paused():
    from quantara_engine.pipeline.candle_processor import CandleProcessor
    from quantara_engine.domain.types import PortfolioStatus, SignalAction

    portfolio = Portfolio(
        id="pf-1",
        name="Test",
        mode=Mode.PAPER,
        initial_capital=Decimal("2000"),
        balance=Decimal("2000"),
        equity=Decimal("2000"),
        status=PortfolioStatus.ACTIVE,
    )
    from quantara_engine.portfolio.service import PortfolioState

    state = PortfolioState(portfolio=portfolio, positions=[])
    instrument = MagicMock(id="inst-eth", symbol="ETHUSD")
    instance = MagicMock(id="si-1", timeframe="5m", strategy_slug="orb")
    risk_profile = MagicMock()
    store = MagicMock()
    store.get_settings_dict.return_value = {
        "trading_control_state": {"state": "running"},
        "asset_trading_controls": {"research": {"ETHUSD": True}, "live_sim": {}},
    }
    store.has_duplicate_entry_for_signal.return_value = False
    store.opportunity_consumed.return_value = False

    processor = CandleProcessor(
        portfolio_state=state,
        strategy_instance=instance,
        instrument=instrument,
        risk_profile=risk_profile,
        broker=MagicMock(),
        store=store,
        allow_live_execution=True,
        execution_now=datetime(2026, 9, 13, 12, 0, tzinfo=timezone.utc),
    )
    processor._log = MagicMock()
    signal = MagicMock(action=SignalAction.BUY, metadata={})
    candle = Candle(
        instrument_id="inst-eth",
        timeframe="5m",
        timestamp=datetime(2026, 9, 13, 12, 0, tzinfo=timezone.utc),
        open=Decimal("3000"),
        high=Decimal("3010"),
        low=Decimal("2990"),
        close=Decimal("3005"),
        volume=Decimal("1"),
    )
    processor._handle_trade_signal(signal, candle, "sig-1", [])
    processor._log.assert_called_once()
    assert processor._log.call_args[0][1].value == "trading_halted"
