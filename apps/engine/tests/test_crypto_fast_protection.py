"""Tests for 1-minute BTC/ETH position protection."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

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
from quantara_engine.execution.crypto_fast_protection import (
    FAST_PROTECTION_IDEMPOTENCY_PREFIX,
    is_fast_protection_crypto,
    run_crypto_fast_protection,
)
from quantara_engine.execution.position_management import (
    manage_all_open_positions,
    process_position_management,
)
from quantara_engine.live_sim.position_management import process_live_sim_exits
from quantara_engine.market_data.polling import FAST_PROTECTION_TIMEFRAME
from quantara_engine.portfolio.service import PortfolioState


def _candle_1m(ts: datetime, *, high: str, low: str = "3500", close: str = "3520") -> Candle:
    return Candle(
        instrument_id="eth",
        timeframe=FAST_PROTECTION_TIMEFRAME,
        timestamp=ts,
        open=Decimal("3510"),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
        volume=Decimal("1"),
    )


def _long_eth(*, sl: str = "3480", tp: str = "3600", timeframe: str = "15m") -> Position:
    return Position(
        id="pos-eth",
        portfolio_id="p1",
        strategy_instance_id="si1",
        instrument_id="eth",
        direction=Direction.LONG,
        quantity=Decimal("0.1"),
        entry_price=Decimal("3500"),
        stop_loss=Decimal(sl),
        take_profit=Decimal(tp),
        current_price=Decimal("3500"),
        status=PositionStatus.OPEN,
        opened_at=datetime(2026, 9, 12, 10, 0, tzinfo=timezone.utc),
        strategy_version_id="sv1",
    )


def _instrument_eth() -> Instrument:
    return Instrument(
        id="eth",
        symbol="ETHUSD",
        name="ETH/USD",
        asset_class="crypto",
        quantity_step=Decimal("0.0001"),
        min_quantity=Decimal("0.0001"),
    )


def _instance_eth(timeframe: str = "15m") -> StrategyInstance:
    return StrategyInstance(
        id="si1",
        portfolio_id="p1",
        strategy_slug="momentum-continuation",
        strategy_version="1.0.0",
        strategy_version_id="sv1",
        instrument_id="eth",
        timeframe=timeframe,
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


class _FakeStore:
    def __init__(self, candles: list[Candle], state: PortfolioState, instance: StrategyInstance):
        self.candles = candles
        self.state = state
        self.instance = instance
        self.decisions = []
        self.settings: dict = {"paper_trading_enabled": True}
        self.cursors: dict[str, str] = {}
        self.fast_cursors: dict[str, str] = {}
        self.last_exit = None
        self.trade_closed = False

    def get_settings_dict(self):
        return {
            **self.settings,
            "position_management_cursors": self.cursors,
            "crypto_fast_protection_cursors": self.fast_cursors,
        }

    def update_settings(self, key, value, description=None, flush=True):
        if key == "position_management_cursors":
            self.cursors = value
        elif key == "crypto_fast_protection_cursors":
            self.fast_cursors = value
        else:
            self.settings[key] = value

    def list_candles(self, instrument_id, timeframe, since=None, limit=None):
        rows = [
            c
            for c in self.candles
            if c.timeframe == timeframe and (since is None or c.timestamp >= since)
        ]
        rows.sort(key=lambda c: c.timestamp)
        return rows[:limit] if limit else rows

    def list_recent_candles(self, instrument_id, timeframe, limit=1):
        rows = [c for c in self.candles if c.timeframe == timeframe]
        return rows[-limit:]

    def latest_candle_timestamp(self, instrument_id, timeframe):
        rows = [c for c in self.candles if c.timeframe == timeframe]
        return rows[-1].timestamp if rows else None

    def load_portfolio_state(self, portfolio_id):
        return self.state

    def update_open_position_mark(self, position_id, mark, upnl, flush=True):
        pos = next(p for p in self.state.open_positions() if p.id == position_id)
        pos.current_price = mark
        pos.unrealized_pnl = upnl

    def sync_portfolios_financial_state_from_ledger(self, portfolios, flush=False):
        pass

    def build_currency_context_for_instruments(self, instruments):
        from quantara_engine.portfolio.currency import CurrencyContext, FxRateTable

        return CurrencyContext({i.id: i for i in instruments}, FxRateTable.usd_only())

    def persist_exit_execution(self, **kwargs):
        self.last_exit = kwargs
        self.trade_closed = True

    def save_decision(self, decision):
        self.decisions.append(decision)

    def save_snapshot(self, snap):
        self.last_snapshot = snap

    def trade_exists_for_position(self, position_id):
        return self.trade_closed

    def upsert_candle(self, candle):
        self.candles.append(candle)

    def list_all_competition_entries(self):
        return [], [], [
            {
                "instance": self.instance,
                "portfolio": self.state.portfolio,
            }
        ]

    def get_instrument_by_id(self, instrument_id):
        return _instrument_eth() if instrument_id == "eth" else None

    def get_instrument_by_symbol(self, symbol):
        return _instrument_eth() if symbol == "ETHUSD" else None

    def session(self):
        return MagicMock()


@pytest.mark.parametrize("strategy_tf", ["5m", "15m", "1h"])
def test_1m_sl_closes_before_strategy_bar(strategy_tf: str):
    """15m/1h/5m strategy entry cadence unchanged; SL exits on 1m bar."""
    sl_1m = _candle_1m(
        datetime(2026, 9, 12, 10, 3, tzinfo=timezone.utc),
        high="3510",
        low="3475",
        close="3485",
    )
    pos = _long_eth(sl="3480")
    state = _portfolio_state(pos)
    instance = _instance_eth(timeframe=strategy_tf)
    store = _FakeStore([sl_1m], state, instance)
    now = datetime(2026, 9, 12, 10, 4, tzinfo=timezone.utc)

    with patch("quantara_engine.broker.execution_bridge.execute_through_broker", return_value=None):
        result = process_position_management(
            store,
            position=pos,
            instance=instance,
            instrument=_instrument_eth(),
            now=now,
            monitor_timeframe=FAST_PROTECTION_TIMEFRAME,
            execution_timeframe=FAST_PROTECTION_TIMEFRAME,
            idempotency_prefix=FAST_PROTECTION_IDEMPOTENCY_PREFIX,
        )

    assert result["status"] == "closed"
    assert result["exit_reason"] == "sl"
    assert result["exit_candle"] == sl_1m.timestamp.isoformat()
    assert len(state.open_positions()) == 0


@pytest.mark.parametrize("strategy_tf", ["5m", "15m", "1h"])
def test_1m_tp_closes_before_strategy_bar(strategy_tf: str):
    tp_1m = _candle_1m(
        datetime(2026, 9, 12, 10, 7, tzinfo=timezone.utc),
        high="3610",
        low="3590",
        close="3605",
    )
    pos = _long_eth(tp="3600")
    state = _portfolio_state(pos)
    instance = _instance_eth(timeframe=strategy_tf)
    store = _FakeStore([tp_1m], state, instance)
    now = datetime(2026, 9, 12, 10, 8, tzinfo=timezone.utc)

    with patch("quantara_engine.broker.execution_bridge.execute_through_broker", return_value=None):
        result = process_position_management(
            store,
            position=pos,
            instance=instance,
            instrument=_instrument_eth(),
            now=now,
            monitor_timeframe=FAST_PROTECTION_TIMEFRAME,
            execution_timeframe=FAST_PROTECTION_TIMEFRAME,
            idempotency_prefix=FAST_PROTECTION_IDEMPOTENCY_PREFIX,
        )

    assert result["status"] == "closed"
    assert result["exit_reason"] == "tp"
    assert any(d.decision_type == DecisionType.TP_TRIGGERED for d in store.decisions)


def test_no_fetch_without_open_crypto_positions():
    store = MagicMock()
    store.get_settings_dict.return_value = {"paper_trading_enabled": True}
    with patch(
        "quantara_engine.trading.trading_controls.load_trading_control",
        return_value=MagicMock(),
    ), patch(
        "quantara_engine.trading.trading_controls.allows_position_management",
        return_value=True,
    ), patch(
        "quantara_engine.execution.crypto_fast_protection._collect_research_crypto_work",
        return_value=[],
    ), patch(
        "quantara_engine.execution.crypto_fast_protection._collect_live_sim_crypto_rows",
        return_value=[],
    ):
        report = run_crypto_fast_protection(store, datetime.now(timezone.utc))

    assert report["status"] == "skipped"
    assert report["reason"] == "no_open_crypto_positions"
    assert report["fetches"] == 0


def test_btc_eth_both_open_two_fetches():
    store = MagicMock()
    store.get_settings_dict.return_value = {"paper_trading_enabled": True}
    eth = _instrument_eth()
    btc = Instrument(
        id="btc",
        symbol="BTCUSD",
        name="BTC/USD",
        asset_class="crypto",
        quantity_step=Decimal("0.0001"),
        min_quantity=Decimal("0.0001"),
    )
    pos_eth = _long_eth()
    pos_btc = Position(
        id="pos-btc",
        portfolio_id="p1",
        strategy_instance_id="si1",
        instrument_id="btc",
        direction=Direction.LONG,
        quantity=Decimal("0.01"),
        entry_price=Decimal("60000"),
        stop_loss=Decimal("59000"),
        take_profit=Decimal("62000"),
        current_price=Decimal("60000"),
        status=PositionStatus.OPEN,
        opened_at=datetime(2026, 9, 12, 10, 0, tzinfo=timezone.utc),
        strategy_version_id="sv1",
    )
    instance = _instance_eth("15m")

    with patch(
        "quantara_engine.trading.trading_controls.load_trading_control",
        return_value=MagicMock(),
    ), patch(
        "quantara_engine.trading.trading_controls.allows_position_management",
        return_value=True,
    ), patch(
        "quantara_engine.execution.crypto_fast_protection._collect_research_crypto_work",
        return_value=[(pos_eth, instance, eth), (pos_btc, instance, btc)],
    ), patch(
        "quantara_engine.execution.crypto_fast_protection._collect_live_sim_crypto_rows",
        return_value=[],
    ), patch(
        "quantara_engine.execution.crypto_fast_protection._fetch_and_store_1m",
        return_value=[],
    ) as fetch_mock, patch(
        "quantara_engine.execution.crypto_fast_protection._process_research_crypto",
        return_value={"checked": 2, "closed": 0},
    ), patch(
        "quantara_engine.execution.crypto_fast_protection._process_live_sim_crypto",
        return_value={"checked": 0, "closed": 0},
    ), patch(
        "quantara_engine.execution.crypto_fast_protection.BrokerExecutionService",
    ):
        store.get_instrument_by_symbol.side_effect = lambda sym: eth if sym == "ETHUSD" else btc
        store.latest_candle_timestamp.return_value = None
        store.list_candles.return_value = []
        report = run_crypto_fast_protection(store, datetime.now(timezone.utc))

    assert report["status"] == "success"
    assert report["fetches"] == 2
    assert fetch_mock.call_count == 2


def test_manage_all_open_positions_skips_crypto():
    store = MagicMock()
    eth = _instrument_eth()
    pos = _long_eth()
    instance = _instance_eth("15m")
    store.get_settings_dict.return_value = {"paper_trading_enabled": True}
    store.list_all_competition_entries.return_value = (
        [],
        [],
        [{"instance": instance, "portfolio": MagicMock(id="p1")}],
    )
    store.batch_load_portfolio_states.return_value = {"p1": _portfolio_state(pos)}
    store.batch_open_positions_by_portfolio.return_value = {"p1": [pos]}
    store.get_instrument_by_id.return_value = eth

    with patch(
        "quantara_engine.trading.trading_controls.load_trading_control",
        return_value=MagicMock(),
    ), patch(
        "quantara_engine.trading.trading_controls.allows_position_management",
        return_value=True,
    ), patch(
        "quantara_engine.trading.trading_controls.requires_flatten",
        return_value=False,
    ), patch(
        "quantara_engine.portfolio.currency.build_currency_context",
        return_value=MagicMock(),
    ), patch(
        "quantara_engine.execution.position_management._get_cursors",
        return_value={},
    ), patch(
        "quantara_engine.execution.position_management.process_position_management",
    ) as pm_mock:
        report = manage_all_open_positions(store, datetime.now(timezone.utc))

    pm_mock.assert_not_called()
    assert report["positions_attempted"] == 0
    assert report["positions_closed"] == 0


def test_live_sim_exits_skips_crypto():
    store = MagicMock()
    store.session.execute.return_value.mappings.return_value.first.return_value = {"id": "acct-1"}
    store.session.execute.return_value.mappings.return_value.all.return_value = [
        {
            "id": "ls-1",
            "instrument_id": "eth",
            "direction": "long",
            "quantity": Decimal("0.1"),
            "entry_price": Decimal("3500"),
            "stop_loss": Decimal("3480"),
            "take_profit": Decimal("3600"),
            "timeframe": "15m",
            "canonical_opportunity_key": "k",
            "symbol": "ETHUSD",
        }
    ]

    with patch(
        "quantara_engine.live_sim.position_management.execute_through_broker",
    ) as broker_mock:
        result = process_live_sim_exits(store, datetime.now(timezone.utc))

    broker_mock.assert_not_called()
    assert result["closed"] == 0


def test_is_fast_protection_crypto_symbols():
    assert is_fast_protection_crypto("BTCUSD")
    assert is_fast_protection_crypto("BTC/USD")
    assert is_fast_protection_crypto("ETHUSD")
    assert not is_fast_protection_crypto("XAUUSD")
    assert not is_fast_protection_crypto("NVDA")
