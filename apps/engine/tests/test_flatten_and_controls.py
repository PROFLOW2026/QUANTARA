"""Flatten cycle and trading control integration."""

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

from quantara_engine.domain.types import (
    Candle,
    Direction,
    Mode,
    Portfolio,
    Position,
    PositionStatus,
)
from quantara_engine.portfolio.balance_reconciliation import reconcile_portfolio_balance
from quantara_engine.portfolio.service import PortfolioState
from quantara_engine.trading.trading_controls import (
    TradingControlState,
    allows_new_entries,
    allows_strategy_evaluation,
    load_trading_control,
    transition_to,
)


def test_flatten_blocks_new_entries():
    snap = load_trading_control({"trading_control_state": {"state": "flattening"}})
    assert not allows_new_entries(snap)
    assert not allows_strategy_evaluation(snap)


def test_stopped_blocks_strategy_and_entries():
    snap = load_trading_control({"trading_control_state": {"state": "stopped"}})
    assert not allows_new_entries(snap)
    assert not allows_strategy_evaluation(snap)


def test_transition_running_clears_flatten_metadata():
    store = MagicMock()
    store.get_settings_dict.return_value = {
        "trading_control_state": {
            "state": "stopped",
            "flatten_started_at": "2026-01-01T00:00:00+00:00",
            "pending_market_reopen": ["NVDA"],
        }
    }
    store.update_settings = MagicMock()
    store.cancel_stale_pending_intents = MagicMock(return_value=0)
    snap = transition_to(store, TradingControlState.RUNNING)
    assert snap.state == TradingControlState.RUNNING
    assert snap.pending_market_reopen == []


def test_flatten_skips_closed_market():
    from quantara_engine.execution.flatten_positions import process_flatten_cycle

    store = MagicMock()
    store.get_settings_dict.return_value = {"trading_control_state": {"state": "flattening"}}
    store.list_recent_candles.return_value = []
    store.count_open_competition_positions.return_value = 1
    store.update_settings = MagicMock()

    portfolio = Portfolio(
        id="p1",
        name="T",
        mode=Mode.PAPER,
        initial_capital=Decimal("2000"),
        balance=Decimal("2000"),
        equity=Decimal("2000"),
    )
    pos = Position(
        id="pos1",
        portfolio_id="p1",
        strategy_instance_id="si1",
        instrument_id="inst-nvda",
        direction=Direction.LONG,
        quantity=Decimal("1"),
        entry_price=Decimal("100"),
        stop_loss=Decimal("90"),
        take_profit=None,
        current_price=Decimal("100"),
        status=PositionStatus.OPEN,
    )
    from quantara_engine.portfolio.service import PortfolioState

    states = {"p1": PortfolioState(portfolio=portfolio, positions=[pos])}
    instance = MagicMock(id="si1", timeframe="5m")
    instrument = MagicMock(id="inst-nvda", symbol="NVDA")

    with patch("quantara_engine.execution.flatten_positions._asset_tradable", return_value=False):
        with patch("quantara_engine.trading.trading_controls.save_trading_control"):
            report = process_flatten_cycle(
                store,
                datetime(2026, 1, 4, 3, 0, tzinfo=timezone.utc),
                portfolio_states=states,
                open_by_portfolio={"p1": [pos]},
                instance_by_id={"si1": instance},
                instrument_cache={"inst-nvda": instrument},
                pending_exits=[],
            )
    assert report["flatten_closed"] == 0
    assert "NVDA" in report["awaiting_reopen"]


def _flatten_store(*, open_count_after: int = 0) -> MagicMock:
    store = MagicMock()
    store.get_settings_dict.return_value = {"trading_control_state": {"state": "flattening"}}
    store.count_open_competition_positions.return_value = open_count_after
    saved: dict = {}

    def _save_settings(key, value):
        if key == "trading_control_state":
            saved["control"] = value

    store.update_settings.side_effect = _save_settings
    return store, saved


def _open_long_position(
    *,
    portfolio_id: str,
    instrument_id: str,
    symbol: str,
    entry: str,
    qty: str = "0.01",
) -> tuple[Portfolio, Position, PortfolioState]:
    initial = Decimal("2000")
    portfolio = Portfolio(
        id=portfolio_id,
        name="Test",
        mode=Mode.PAPER,
        initial_capital=initial,
        balance=initial,
        equity=initial,
        unrealized_pnl=Decimal("0"),
    )
    pos = Position(
        id="pos1",
        portfolio_id=portfolio_id,
        strategy_instance_id="si1",
        instrument_id=instrument_id,
        direction=Direction.LONG,
        quantity=Decimal(qty),
        entry_price=Decimal(entry),
        stop_loss=Decimal(str(Decimal(entry) * Decimal("0.95"))),
        take_profit=None,
        current_price=Decimal(entry),
        status=PositionStatus.OPEN,
        opened_at=datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc),
    )
    state = PortfolioState(portfolio=portfolio, positions=[pos])
    return portfolio, pos, state


def _tradable_candle(instrument_id: str, *, open_: str, close: str, ts: datetime) -> Candle:
    o = Decimal(open_)
    c = Decimal(close)
    return Candle(
        instrument_id=instrument_id,
        timeframe="5m",
        timestamp=ts,
        open=o,
        high=max(o, c) + Decimal("10"),
        low=min(o, c) - Decimal("10"),
        close=c,
        volume=Decimal("1"),
    )


def test_flatten_crypto_open_market_closes_and_stops():
    """BTC position closes at tradable price during FLATTENING; state → STOPPED when flat."""
    from quantara_engine.execution.flatten_positions import process_flatten_cycle

    now = datetime(2026, 9, 11, 14, 5, tzinfo=timezone.utc)
    candle_ts = datetime(2026, 9, 11, 14, 0, tzinfo=timezone.utc)
    candle = _tradable_candle("inst-btc", open_="95000", close="95100", ts=candle_ts)

    portfolio, pos, state = _open_long_position(
        portfolio_id="p-crypto",
        instrument_id="inst-btc",
        symbol="BTCUSD",
        entry="94000",
    )
    store, saved = _flatten_store(open_count_after=0)
    store.list_recent_candles.return_value = [candle]

    instance = MagicMock(id="si1", timeframe="5m")
    instrument = MagicMock(id="inst-btc", symbol="BTCUSD")

    assert not allows_new_entries(load_trading_control(store.get_settings_dict()))

    pending_exits: list = []
    with patch("quantara_engine.execution.flatten_positions._asset_tradable", return_value=True):
        report = process_flatten_cycle(
            store,
            now,
            portfolio_states={"p-crypto": state},
            open_by_portfolio={"p-crypto": [pos]},
            instance_by_id={"si1": instance},
            instrument_cache={"inst-btc": instrument},
            pending_exits=pending_exits,
        )

    assert report["flatten_closed"] == 1
    assert pos.status == PositionStatus.CLOSED
    assert len(pending_exits) == 1
    assert pending_exits[0]["trade"].realized_pnl is not None

    realized = pending_exits[0]["trade"].realized_pnl
    state.portfolio.unrealized_pnl = Decimal("0")
    state.recalculate_equity({})
    recon = reconcile_portfolio_balance(state.portfolio, realized)
    assert recon.balance_difference == Decimal("0.00")
    assert recon.equity_difference == Decimal("0.00")
    assert state.portfolio.balance == Decimal("2000") + realized

    assert report["trading_control_state"] == TradingControlState.STOPPED.value
    assert saved["control"]["state"] == TradingControlState.STOPPED.value


def test_flatten_us_equity_rth_open_closes_and_stops():
    """NVDA position closes during RTH FLATTENING at executable price; accounting + STOPPED."""
    from quantara_engine.execution.flatten_positions import process_flatten_cycle

    # 18:00 UTC = 14:00 ET on a weekday — inside US RTH
    now = datetime(2026, 9, 11, 18, 5, tzinfo=timezone.utc)
    candle_ts = datetime(2026, 9, 11, 18, 0, tzinfo=timezone.utc)
    candle = _tradable_candle("inst-nvda", open_="180.50", close="180.75", ts=candle_ts)

    portfolio, pos, state = _open_long_position(
        portfolio_id="p-us",
        instrument_id="inst-nvda",
        symbol="NVDA",
        entry="175.00",
        qty="1",
    )
    store, saved = _flatten_store(open_count_after=0)
    store.list_recent_candles.return_value = [candle]

    instance = MagicMock(id="si1", timeframe="5m")
    instrument = MagicMock(id="inst-nvda", symbol="NVDA")

    assert not allows_new_entries(load_trading_control(store.get_settings_dict()))

    pending_exits: list = []
    report = process_flatten_cycle(
        store,
        now,
        portfolio_states={"p-us": state},
        open_by_portfolio={"p-us": [pos]},
        instance_by_id={"si1": instance},
        instrument_cache={"inst-nvda": instrument},
        pending_exits=pending_exits,
    )

    assert report["flatten_closed"] == 1
    assert pos.status == PositionStatus.CLOSED
    assert len(pending_exits) == 1
    fill_price = pending_exits[0]["fill"].fill_price
    assert abs(fill_price - Decimal("180.50")) <= Decimal("1.00")

    realized = pending_exits[0]["trade"].realized_pnl
    state.portfolio.unrealized_pnl = Decimal("0")
    state.recalculate_equity({})
    recon = reconcile_portfolio_balance(state.portfolio, realized)
    assert recon.balance_difference == Decimal("0.00")
    assert recon.equity_difference == Decimal("0.00")

    assert report["trading_control_state"] == TradingControlState.STOPPED.value
    assert saved["control"]["state"] == TradingControlState.STOPPED.value
