"""PM restart / backlog correctness with WorkerCandleCache."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import patch

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
from quantara_engine.execution.position_management import (
    MAX_CANDLES_PER_POSITION_PER_RUN,
    _prefetch_candles_by_pair,
    get_last_managed_timestamp,
    process_position_management,
)
from quantara_engine.market_data.candle_working_set import (
    WorkerCandleCache,
    reset_worker_candle_cache,
)
from quantara_engine.market_data.polling import STRATEGY_MIN_CANDLES
from quantara_engine.portfolio.service import PortfolioState
from tests.egress_test_support import IncrementalCandleBackingStore, make_candle_series


LOOKBACK = STRATEGY_MIN_CANDLES + 50


def _baseline_prefetch(store, work, cursors):
    """Reference path — direct oldest-first DB reads (pre-cache behavior)."""
    from datetime import datetime as dt

    floors: dict[tuple[str, str], datetime] = {}
    for position, instance, instrument in work:
        key = (instrument.id, instance.timeframe)
        last_raw = cursors.get(position.id)
        last_managed = dt.fromisoformat(last_raw.replace("Z", "+00:00")) if last_raw else None
        floor = last_managed or position.opened_at
        if floor is None:
            continue
        prev = floors.get(key)
        if prev is None or floor < prev:
            floors[key] = floor

    out: dict[tuple[str, str], list] = {}
    for (instrument_id, timeframe), since in floors.items():
        out[(instrument_id, timeframe)] = store.list_candles(
            instrument_id,
            timeframe,
            since=since,
            limit=MAX_CANDLES_PER_POSITION_PER_RUN,
        )
    return out


def _short_position(*, opened_at: datetime, sl: str = "105") -> Position:
    return Position(
        id="pos-1",
        portfolio_id="p1",
        strategy_instance_id="si1",
        instrument_id="btc",
        direction=Direction.SHORT,
        quantity=Decimal("0.01"),
        entry_price=Decimal("100"),
        stop_loss=Decimal(sl),
        take_profit=Decimal("90"),
        current_price=Decimal("100"),
        status=PositionStatus.OPEN,
        opened_at=opened_at,
        strategy_version_id="sv1",
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


class _PmStoreAdapter:
    """Minimal store for PM tests backed by IncrementalCandleBackingStore."""

    def __init__(self, backing: IncrementalCandleBackingStore, state: PortfolioState, instance: StrategyInstance):
        self.backing = backing
        self.state = state
        self.instance = instance
        self.decisions = []
        self.cursors: dict[str, str] = {}
        self.last_exit = None

    def get_settings_dict(self):
        return {"position_management_cursors": self.cursors}

    def update_settings(self, key, value, description=None):
        if key == "position_management_cursors":
            self.cursors = value

    def list_candles(self, instrument_id, timeframe, since=None, limit=None):
        return self.backing.list_candles(instrument_id, timeframe, since=since, limit=limit)

    def list_recent_candles(self, instrument_id, timeframe, limit=50):
        return self.backing.list_recent_candles(instrument_id, timeframe, limit=limit)

    def list_candles_after(self, instrument_id, timeframe, after, limit=None):
        return self.backing.list_candles_after(instrument_id, timeframe, after, limit=limit)

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

    def save_decision(self, decision):
        self.decisions.append(decision)

    def save_snapshot(self, snap):
        self.last_snapshot = snap


def test_candles_since_oldest_first_not_newest_tail():
    reset_worker_candle_cache()
    backing = IncrementalCandleBackingStore()
    candles = make_candle_series(500, instrument_id="btc", timeframe="5m")
    backing.seed("btc", "5m", candles)
    cache = WorkerCandleCache()
    since = candles[1].timestamp
    rows = cache.candles_since(
        backing,
        "btc",
        "5m",
        since,
        lookback=LOOKBACK,
        limit=200,
    )
    assert rows[0].timestamp == since
    assert len(rows) == 200
    assert rows[-1].timestamp == candles[200].timestamp


def test_candles_since_db_catchup_when_cursor_older_than_cache_window():
    reset_worker_candle_cache()
    backing = IncrementalCandleBackingStore()
    candles = make_candle_series(500, instrument_id="btc", timeframe="5m")
    backing.seed("btc", "5m", candles)
    cache = WorkerCandleCache()
    cache.get_window(backing, "btc", "5m", lookback=LOOKBACK)
    since = candles[0].timestamp
    rows = cache.candles_since(
        backing,
        "btc",
        "5m",
        since,
        lookback=LOOKBACK,
        limit=200,
    )
    assert backing.egress_metrics.query_labels.get("list_candles") == 1
    assert rows[0].timestamp == since
    assert len(rows) == 200


def test_early_sl_executed_after_cold_restart():
    reset_worker_candle_cache()
    candles = make_candle_series(500, instrument_id="btc", timeframe="5m")
    sl_index = 49
    candles[sl_index] = Candle(
        instrument_id="btc",
        timeframe="5m",
        timestamp=candles[sl_index].timestamp,
        open=Decimal("149"),
        high=Decimal("160"),
        low=Decimal("148"),
        close=Decimal("155"),
        volume=Decimal("1"),
    )
    opened_at = candles[0].timestamp
    pos = _short_position(opened_at=opened_at, sl="156")
    state = _portfolio_state(pos)
    backing = IncrementalCandleBackingStore()
    backing.seed("btc", "5m", candles)
    store = _PmStoreAdapter(backing, state, _instance())
    store.cursors[pos.id] = opened_at.isoformat()
    now = candles[-1].timestamp + timedelta(minutes=10)

    work = [(pos, _instance(), _instrument())]
    prefetched = _prefetch_candles_by_pair(store, work, store.cursors)
    with patch("quantara_engine.broker.execution_bridge.execute_through_broker", return_value=None):
        result = process_position_management(
            store,
            position=pos,
            instance=_instance(),
            instrument=_instrument(),
            now=now,
            cursors=store.cursors,
            portfolio_state=state,
            prefetched=prefetched.get(("btc", "5m")),
        )

    assert result["status"] == "closed"
    assert result["exit_reason"] == "sl"
    assert result["exit_candle"] == candles[sl_index].timestamp.isoformat()
    assert len(state.open_positions()) == 0


def test_cursor_advances_sequentially_over_200_plus_backlog():
    reset_worker_candle_cache()
    candles = make_candle_series(500, instrument_id="btc", timeframe="5m")
    opened_at = candles[0].timestamp
    pos = _short_position(opened_at=opened_at, sl="1000")
    state = _portfolio_state(pos)
    backing = IncrementalCandleBackingStore()
    backing.seed("btc", "5m", candles)
    store = _PmStoreAdapter(backing, state, _instance())
    store.cursors[pos.id] = opened_at.isoformat()
    now = candles[-1].timestamp + timedelta(minutes=10)

    seen: list[datetime] = []
    for _ in range(4):
        work = [(pos, _instance(), _instrument())]
        prefetched = _prefetch_candles_by_pair(store, work, store.cursors)
        batch = prefetched.get(("btc", "5m"), [])
        if not batch:
            break
        result = process_position_management(
            store,
            position=pos,
            instance=_instance(),
            instrument=_instrument(),
            now=now,
            cursors=store.cursors,
            portfolio_state=state,
            prefetched=batch,
        )
        if result["status"] == "managed":
            seen.append(datetime.fromisoformat(store.cursors[pos.id].replace("Z", "+00:00")))
        else:
            break

    assert len(seen) >= 3
    for earlier, later in zip(seen, seen[1:]):
        assert later > earlier
    last_cursor = datetime.fromisoformat(store.cursors[pos.id].replace("Z", "+00:00"))
    assert last_cursor >= candles[200].timestamp


def test_cache_prefetch_matches_baseline_oldest_first():
    reset_worker_candle_cache()
    candles = make_candle_series(500, instrument_id="btc", timeframe="5m")
    opened_at = candles[0].timestamp
    pos = _short_position(opened_at=opened_at)
    backing = IncrementalCandleBackingStore()
    backing.seed("btc", "5m", candles)
    store = _PmStoreAdapter(backing, _portfolio_state(pos), _instance())
    store.cursors[pos.id] = opened_at.isoformat()
    work = [(pos, _instance(), _instrument())]

    baseline = _baseline_prefetch(store, work, store.cursors)
    cache_rows = _prefetch_candles_by_pair(store, work, store.cursors)

    assert list(baseline[("btc", "5m")]) == list(cache_rows[("btc", "5m")])


def test_steady_state_uses_incremental_tail_after_caught_up():
    reset_worker_candle_cache()
    backing = IncrementalCandleBackingStore()
    candles = make_candle_series(500, instrument_id="btc", timeframe="5m")
    backing.seed("btc", "5m", candles)
    cache = WorkerCandleCache()
    since = candles[400].timestamp
    cache.get_window(backing, "btc", "5m", lookback=LOOKBACK)
    backing.egress_metrics.reset()
    rows = cache.candles_since(
        backing,
        "btc",
        "5m",
        since,
        lookback=LOOKBACK,
        limit=200,
    )
    assert backing.egress_metrics.query_labels.get("list_candles", 0) == 0
    assert rows[0].timestamp >= since
    assert len(rows) <= 200
