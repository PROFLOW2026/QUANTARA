"""Tests for independent position management (unattended exit safety)."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import patch

from quantara_engine.domain.types import (
    Candle,
    DecisionType,
    Direction,
    Instrument,
    Mode,
    Portfolio,
    PortfolioStatus,
    Position,
    PositionStatus,
    StrategyInstance,
)
from quantara_engine.execution.position_management import process_position_management
from quantara_engine.portfolio.service import PortfolioState
class _FakeStore:
    def __init__(self, candles: list[Candle], state: PortfolioState, instance: StrategyInstance):
        self.candles = candles
        self.state = state
        self.instance = instance
        self.decisions = []
        self.settings: dict = {}
        self.cursors: dict[str, str] = {}
        self.last_exit = None

    def get_settings_dict(self):
        return {**self.settings, "position_management_cursors": self.cursors}

    def update_settings(self, key, value, description=None):
        if key == "position_management_cursors":
            self.cursors = value
        else:
            self.settings[key] = value

    def list_candles(self, instrument_id, timeframe, since=None, limit=None):
        rows = [c for c in self.candles if since is None or c.timestamp >= since]
        return rows[:limit] if limit else rows

    def list_recent_candles(self, instrument_id, timeframe, limit=1):
        return self.candles[-limit:]

    def load_portfolio_state(self, portfolio_id):
        return self.state

    def update_open_position_mark(self, position_id, mark, upnl, flush=True):
        pos = next(p for p in self.state.open_positions() if p.id == position_id)
        pos.current_price = mark
        pos.unrealized_pnl = upnl

    def update_portfolio(self, portfolio, flush=True):
        self.state.portfolio = portfolio

    def persist_exit_execution(self, **kwargs):
        self.last_exit = kwargs

    def save_decision(self, decision):
        self.decisions.append(decision)

    def save_snapshot(self, snap):
        self.last_snapshot = snap

    def get_active_strategy_instance(self, portfolio_id):
        return self.instance


def _short_position(*, sl: str = "78617.35", entry: str = "78483.51") -> Position:
    return Position(
        id="pos-1",
        portfolio_id="p1",
        strategy_instance_id="si1",
        instrument_id="btc",
        direction=Direction.SHORT,
        quantity=Decimal("0.01"),
        entry_price=Decimal(entry),
        stop_loss=Decimal(sl),
        take_profit=Decimal("78228.82"),
        current_price=Decimal(entry),
        status=PositionStatus.OPEN,
        opened_at=datetime(2026, 9, 8, 22, 40, tzinfo=timezone.utc),
        strategy_version_id="sv1",
    )


def _candle(ts: datetime, high: str, low: str = "78300", close: str = "78400") -> Candle:
    return Candle(
        instrument_id="btc",
        timeframe="5m",
        timestamp=ts,
        open=Decimal("78400"),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
        volume=Decimal("1"),
    )


def _instrument() -> Instrument:
    return Instrument(
        id="btc",
        symbol="BTCUSD",
        name="BTC/USD",
        asset_class="crypto",
        quantity_step=Decimal("0.0001"),
        min_quantity=Decimal("0.0001"),
    )


def _instance() -> StrategyInstance:
    return StrategyInstance(
        id="si1",
        portfolio_id="p1",
        strategy_slug="gold-trend-pullback",
        strategy_version="1.0.0",
        strategy_version_id="sv1",
        instrument_id="btc",
        timeframe="5m",
        risk_profile_id="rp1",
        parameter_overrides={},
    )


def _portfolio_state(pos: Position) -> PortfolioState:
    return PortfolioState(
        portfolio=Portfolio(
            id="p1",
            name="Test",
            mode=Mode.PAPER,
            initial_capital=Decimal("2000"),
            balance=Decimal("2000"),
            equity=Decimal("2000"),
            status=PortfolioStatus.ACTIVE,
        ),
        positions=[pos],
    )


def test_sl_closes_even_when_strategy_stalled():
    sl_candle = _candle(datetime(2026, 9, 9, 0, 5, tzinfo=timezone.utc), high="78621.3")
    prior = _candle(datetime(2026, 9, 8, 23, 55, tzinfo=timezone.utc), high="78470")
    pos = _short_position()
    state = _portfolio_state(pos)
    store = _FakeStore([prior, sl_candle], state, _instance())
    now = datetime(2026, 9, 9, 0, 10, tzinfo=timezone.utc)

    with patch(
        "quantara_workers.jobs.run_strategy.run_strategy_job",
        side_effect=RuntimeError("stalled"),
    ):
        result = process_position_management(
            store,
            position=pos,
            instance=_instance(),
            instrument=_instrument(),
            now=now,
        )

    assert result["status"] == "closed"
    assert result["exit_reason"] == "sl"
    assert result["exit_candle"] == sl_candle.timestamp.isoformat()
    assert len(state.open_positions()) == 0
    assert any(d.decision_type == DecisionType.SL_TRIGGERED for d in store.decisions)


def test_catchup_closes_at_first_missed_sl_not_latest_price():
    first_sl = _candle(
        datetime(2026, 9, 9, 0, 5, tzinfo=timezone.utc),
        high="78621.3",
        close="78618.9",
    )
    later = _candle(datetime(2026, 9, 9, 0, 30, tzinfo=timezone.utc), high="78737", close="78664")
    pos = _short_position()
    state = _portfolio_state(pos)
    store = _FakeStore([first_sl, later], state, _instance())
    now = datetime(2026, 9, 9, 0, 35, tzinfo=timezone.utc)
    result = process_position_management(
        store,
        position=pos,
        instance=_instance(),
        instrument=_instrument(),
        now=now,
    )
    assert result["status"] == "closed"
    assert result["exit_candle"] == first_sl.timestamp.isoformat()
    assert result["exit_price"] != float(later.close)


def test_mark_updates_without_exit():
    safe = _candle(
        datetime(2026, 9, 8, 23, 55, tzinfo=timezone.utc),
        high="78470",
        close="78448.68",
    )
    pos = _short_position(sl="79000")
    state = _portfolio_state(pos)
    store = _FakeStore([safe], state, _instance())
    now = datetime(2026, 9, 9, 0, 1, tzinfo=timezone.utc)
    result = process_position_management(
        store,
        position=pos,
        instance=_instance(),
        instrument=_instrument(),
        now=now,
    )
    assert result["status"] == "managed"
    assert float(state.open_positions()[0].current_price) == float(safe.close)
    assert float(state.open_positions()[0].unrealized_pnl) == float(
        state.portfolio.unrealized_pnl
    )
