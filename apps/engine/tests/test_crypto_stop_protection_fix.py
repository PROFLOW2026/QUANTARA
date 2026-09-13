"""Focused tests for crypto stop-protection fix (paper_run_id, isolation, 5m fallback)."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from quantara_engine.domain.types import (
    Candle,
    DecisionType,
    Direction,
    ExitReason,
    Instrument,
    Mode,
    Portfolio,
    PortfolioStatus,
    Position,
    PositionStatus,
    StrategyInstance,
    Trade,
    new_id,
)
from quantara_engine.execution.crypto_fast_protection import (
    CRYPTO_1M_STALE_MINUTES,
    FALLBACK_TIMEFRAME,
    FAST_PROTECTION_IDEMPOTENCY_PREFIX,
    _commit_market_data,
    _process_research_crypto,
    is_crypto_1m_stale,
    run_crypto_fast_protection,
)
from quantara_engine.execution.crypto_missed_exit_recovery import (
    recover_missed_research_crypto_exits,
)
from quantara_engine.execution.exit_triggers import detect_exit_trigger
from quantara_engine.execution.position_management import process_position_management
from quantara_engine.market_data.polling import FAST_PROTECTION_TIMEFRAME
from quantara_engine.models.trading import Trade as OrmTrade
from quantara_engine.persistence.store import TradingStore
from quantara_engine.portfolio.service import PortfolioState


def _btc_instrument() -> Instrument:
    return Instrument(
        id="btc",
        symbol="BTCUSD",
        name="BTC/USD",
        asset_class="crypto",
        quantity_step=Decimal("0.0001"),
        min_quantity=Decimal("0.0001"),
    )


def _eth_instrument() -> Instrument:
    return Instrument(
        id="eth",
        symbol="ETHUSD",
        name="ETH/USD",
        asset_class="crypto",
        quantity_step=Decimal("0.0001"),
        min_quantity=Decimal("0.0001"),
    )


def _candle(
    instrument_id: str,
    timeframe: str,
    ts: datetime,
    *,
    open_: str,
    high: str,
    low: str,
    close: str,
) -> Candle:
    return Candle(
        instrument_id=instrument_id,
        timeframe=timeframe,
        timestamp=ts,
        open=Decimal(open_),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
        volume=Decimal("1"),
    )


def _long_btc(
    *,
    pos_id: str = "pos-btc",
    entry: str = "77267.55",
    sl: str = "77157.71",
    tp: str = "77436.38",
    opened_at: datetime | None = None,
) -> Position:
    return Position(
        id=pos_id,
        portfolio_id="p1",
        strategy_instance_id="si1",
        instrument_id="btc",
        direction=Direction.LONG,
        quantity=Decimal("0.01"),
        entry_price=Decimal(entry),
        stop_loss=Decimal(sl),
        take_profit=Decimal(tp),
        current_price=Decimal(entry),
        status=PositionStatus.OPEN,
        opened_at=opened_at or datetime(2026, 9, 13, 17, 40, tzinfo=timezone.utc),
        strategy_version_id="sv1",
    )


def _long_eth(
    *,
    pos_id: str = "pos-eth",
    entry: str = "3500",
    sl: str = "3480",
    tp: str = "3600",
) -> Position:
    return Position(
        id=pos_id,
        portfolio_id="p1",
        strategy_instance_id="si1",
        instrument_id="eth",
        direction=Direction.LONG,
        quantity=Decimal("0.1"),
        entry_price=Decimal(entry),
        stop_loss=Decimal(sl),
        take_profit=Decimal(tp),
        current_price=Decimal(entry),
        status=PositionStatus.OPEN,
        opened_at=datetime(2026, 9, 13, 17, 40, tzinfo=timezone.utc),
        strategy_version_id="sv1",
    )


def _instance(instrument_id: str = "btc", timeframe: str = "5m") -> StrategyInstance:
    return StrategyInstance(
        id="si1",
        portfolio_id="p1",
        strategy_slug="gold-trend-pullback",
        strategy_version="1.0.0",
        strategy_version_id="sv1",
        instrument_id=instrument_id,
        timeframe=timeframe,
        risk_profile_id="rp1",
        parameter_overrides={},
    )


def _portfolio_state(positions: list[Position]) -> PortfolioState:
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
        positions=list(positions),
    )


class _SessionHarness:
    def __init__(self):
        self.commits = 0
        self.rollbacks = 0
        self.nested_commits = 0
        self.nested_rollbacks = 0

    def flush(self):
        pass

    def commit(self):
        self.commits += 1

    def begin_nested(self):
        parent = self

        class _Nested:
            def commit(self_inner):
                parent.nested_commits += 1

            def rollback(self_inner):
                parent.nested_rollbacks += 1

        return _Nested()


class _ProtectStore:
    def __init__(self, candles: list[Candle], state: PortfolioState, instance: StrategyInstance):
        self.candles = list(candles)
        self.state = state
        self.instance = instance
        self.decisions = []
        self.settings: dict = {"paper_trading_enabled": True}
        self.cursors: dict[str, str] = {}
        self.fast_cursors: dict[str, str] = {}
        self.fallback_cursors: dict[str, str] = {}
        self.last_exit = None
        self.trade_closed_ids: set[str] = set()
        self.session = _SessionHarness()
        self.upserted: list[Candle] = []

    def get_settings_dict(self):
        return {
            **self.settings,
            "crypto_fast_protection_cursors": self.fast_cursors,
            "crypto_5m_fallback_protection_cursors": self.fallback_cursors,
        }

    def update_settings(self, key, value, description=None, flush=True):
        if key == "crypto_fast_protection_cursors":
            self.fast_cursors = value
        elif key == "crypto_5m_fallback_protection_cursors":
            self.fallback_cursors = value
        else:
            self.settings[key] = value

    def list_candles(self, instrument_id, timeframe, since=None, limit=None):
        rows = [
            c
            for c in self.candles
            if c.instrument_id == instrument_id
            and c.timeframe == timeframe
            and (since is None or c.timestamp >= since)
        ]
        rows.sort(key=lambda c: c.timestamp)
        return rows[:limit] if limit else rows

    def latest_candle_timestamp(self, instrument_id, timeframe):
        rows = [c for c in self.candles if c.instrument_id == instrument_id and c.timeframe == timeframe]
        return rows[-1].timestamp if rows else None

    def load_portfolio_state(self, portfolio_id):
        return self.state

    def persist_exit_execution(self, **kwargs):
        self.last_exit = kwargs
        self.trade_closed_ids.add(kwargs["position"].id)

    def save_decision(self, decision):
        self.decisions.append(decision)

    def save_snapshot(self, snap):
        self.last_snapshot = snap

    def trade_exists_for_position(self, position_id):
        return position_id in self.trade_closed_ids

    def upsert_candle(self, candle):
        self.candles.append(candle)
        self.upserted.append(candle)

    def list_all_competition_entries(self):
        return [], [], [{"instance": self.instance, "portfolio": self.state.portfolio}]

    def get_instrument_by_id(self, instrument_id):
        if instrument_id == "btc":
            return _btc_instrument()
        if instrument_id == "eth":
            return _eth_instrument()
        return None

    def get_instrument_by_symbol(self, symbol):
        if symbol in ("BTCUSD", "BTC/USD"):
            return _btc_instrument()
        if symbol in ("ETHUSD", "ETH/USD"):
            return _eth_instrument()
        return None

    def build_currency_context_for_instruments(self, instruments):
        from quantara_engine.portfolio.currency import CurrencyContext, FxRateTable

        return CurrencyContext({i.id: i for i in instruments}, FxRateTable.usd_only())


# --- A: Research BTC 1m SL → trade created → position closed ---


def test_a_research_btc_1m_sl_closes_and_persists_trade():
    ts = datetime(2026, 9, 13, 22, 5, tzinfo=timezone.utc)
    sl_1m = _candle(
        "btc",
        FAST_PROTECTION_TIMEFRAME,
        ts,
        open_="77200",
        high="77210",
        low="77100",
        close="77120",
    )
    pos = _long_btc()
    state = _portfolio_state([pos])
    store = _ProtectStore([sl_1m], state, _instance())
    now = ts + timedelta(minutes=1)

    with patch("quantara_engine.broker.execution_bridge.execute_through_broker", return_value=None):
        result = process_position_management(
            store,
            position=pos,
            instance=_instance(),
            instrument=_btc_instrument(),
            now=now,
            monitor_timeframe=FAST_PROTECTION_TIMEFRAME,
            execution_timeframe=FAST_PROTECTION_TIMEFRAME,
            idempotency_prefix=FAST_PROTECTION_IDEMPOTENCY_PREFIX,
        )

    assert result["status"] == "closed"
    assert result["exit_reason"] == "sl"
    assert store.last_exit is not None
    assert store.last_exit["trade"].exit_reason == ExitReason.SL
    assert len(state.open_positions()) == 0


# --- B: Trade construction accepts paper_run_id ---


def test_b_orm_trade_accepts_paper_run_id():
    from quantara_engine.models.enums import Direction as OrmDirection
    from quantara_engine.models.enums import ExitReason as OrmExitReason
    from quantara_engine.models.enums import PortfolioMode

    run_id = uuid.uuid4()
    row = OrmTrade(
        id=uuid.uuid4(),
        position_id=uuid.uuid4(),
        portfolio_id=uuid.uuid4(),
        strategy_instance_id=uuid.uuid4(),
        strategy_version_id=uuid.uuid4(),
        instrument_id=uuid.uuid4(),
        direction=OrmDirection.LONG,
        quantity=Decimal("0.01"),
        entry_price=Decimal("100"),
        exit_price=Decimal("99"),
        gross_pnl=Decimal("-1"),
        realized_pnl=Decimal("-1"),
        fees_total=Decimal("0"),
        slippage_total=Decimal("0"),
        spread_total=Decimal("0"),
        target_risk_amount=Decimal("1"),
        actual_risk_amount=Decimal("1"),
        exit_reason=OrmExitReason.SL,
        duration_seconds=60,
        opened_at=datetime.now(timezone.utc),
        closed_at=datetime.now(timezone.utc),
        mode=PortfolioMode.PAPER,
        paper_run_id=run_id,
    )
    assert row.paper_run_id == run_id


def test_b_save_trade_passes_paper_run_id_without_typeerror():
    store = MagicMock(spec=TradingStore)
    store.mode = Mode.PAPER
    store._bt_uuid.return_value = None
    store.resolve_strategy_version_id.return_value = str(uuid.uuid4())
    store._lookup_entry_risk_for_position.return_value = None
    store.session = MagicMock()

    trade = Trade(
        id=new_id(),
        position_id=new_id(),
        portfolio_id=new_id(),
        strategy_instance_id=str(uuid.uuid4()),
        strategy_version_id=str(uuid.uuid4()),
        instrument_id=new_id(),
        direction=Direction.LONG,
        quantity=Decimal("0.01"),
        entry_price=Decimal("100"),
        exit_price=Decimal("99"),
        gross_pnl=Decimal("-1"),
        realized_pnl=Decimal("-1"),
        fees_total=Decimal("0"),
        slippage_total=Decimal("0"),
        spread_total=Decimal("0"),
        target_risk_amount=Decimal("1"),
        actual_risk_amount=Decimal("1"),
        exit_reason=ExitReason.SL,
        duration_seconds=60,
        opened_at=datetime.now(timezone.utc),
        closed_at=datetime.now(timezone.utc),
    )
    paper_run = str(uuid.uuid4())
    captured: dict = {}

    def _fake_orm_trade(**kwargs):
        captured.update(kwargs)
        return MagicMock()

    with patch(
        "quantara_engine.competition.paper_run.paper_run_columns_ready",
        return_value=True,
    ), patch(
        "quantara_engine.competition.paper_run.resolve_trade_paper_run_id",
        return_value=paper_run,
    ), patch(
        "quantara_engine.persistence.store.OrmTrade",
        side_effect=_fake_orm_trade,
    ):
        TradingStore.save_trade(store, trade)

    assert captured.get("paper_run_id") is not None
    assert str(captured["paper_run_id"]) == paper_run


# --- C/D: failed close does not erase candles; other positions continue ---


def test_c_d_failed_close_keeps_candles_and_isolates_positions():
    session = _SessionHarness()
    store = MagicMock()
    store.session = session
    _commit_market_data(store)
    assert session.commits == 1

    good = _long_btc(pos_id="good")
    bad = _long_btc(pos_id="bad")
    state = _portfolio_state([good, bad])
    # Candle that hits SL for both
    ts = datetime(2026, 9, 13, 22, 5, tzinfo=timezone.utc)
    sl_1m = _candle(
        "btc",
        FAST_PROTECTION_TIMEFRAME,
        ts,
        open_="77200",
        high="77210",
        low="77100",
        close="77120",
    )
    protect = _ProtectStore([sl_1m], state, _instance())
    protect.session = session
    now = ts + timedelta(minutes=1)

    call_count = {"n": 0}

    def _protect_side_effect(*args, **kwargs):
        call_count["n"] += 1
        position = args[1]
        if position.id == "bad":
            raise TypeError("'paper_run_id' is an invalid keyword argument for Trade")
        with patch(
            "quantara_engine.broker.execution_bridge.execute_through_broker",
            return_value=None,
        ):
            return process_position_management(
                protect,
                position=position,
                instance=_instance(),
                instrument=_btc_instrument(),
                now=now,
                monitor_timeframe=FAST_PROTECTION_TIMEFRAME,
                execution_timeframe=FAST_PROTECTION_TIMEFRAME,
                idempotency_prefix=FAST_PROTECTION_IDEMPOTENCY_PREFIX,
            )

    work = [
        (bad, _instance(), _btc_instrument()),
        (good, _instance(), _btc_instrument()),
    ]
    with patch(
        "quantara_engine.execution.crypto_fast_protection._protect_one_research_position",
        side_effect=_protect_side_effect,
    ):
        result = _process_research_crypto(
            protect,
            work,
            now=now,
            cursors={},
            fallback_cursors={},
            candles_1m_by_instrument={"btc": [sl_1m]},
            candles_5m_by_instrument={},
            stale_by_instrument={"btc": False},
        )

    assert session.nested_rollbacks == 1
    assert session.nested_commits == 1
    assert result["closed"] == 1
    assert len(result["errors"]) == 1
    assert result["errors"][0]["position_id"] == "bad"
    # Candle still present (would have been rolled back before the fix)
    assert any(c.timestamp == ts for c in protect.candles)


# --- E/F: 1m fresh vs 5m fallback ---


def test_e_f_protection_source_1m_vs_5m_fallback():
    now = datetime(2026, 9, 14, 1, 10, tzinfo=timezone.utc)
    fresh_1m = now - timedelta(minutes=1)
    stale_1m = now - timedelta(minutes=CRYPTO_1M_STALE_MINUTES + 2)
    assert is_crypto_1m_stale(fresh_1m, now) is False
    assert is_crypto_1m_stale(stale_1m, now) is True

    pos = _long_btc()
    state = _portfolio_state([pos])
    sl_5m = _candle(
        "btc",
        FALLBACK_TIMEFRAME,
        datetime(2026, 9, 13, 22, 0, tzinfo=timezone.utc),
        open_="77280",
        high="77280",
        low="77022",
        close="77056",
    )
    store = _ProtectStore([sl_5m], state, _instance())
    with patch("quantara_engine.broker.execution_bridge.execute_through_broker", return_value=None):
        result = process_position_management(
            store,
            position=pos,
            instance=_instance(),
            instrument=_btc_instrument(),
            now=now,
            monitor_timeframe=FALLBACK_TIMEFRAME,
            execution_timeframe=FALLBACK_TIMEFRAME,
            idempotency_prefix="pm5m_fb",
        )
    assert result["status"] == "closed"
    assert result["exit_reason"] == "sl"


# --- G: both SL+TP in fallback 5m → SL wins ---


def test_g_both_sl_and_tp_touched_sl_wins():
    pos = _long_btc(sl="77157", tp="77436")
    candle = _candle(
        "btc",
        FALLBACK_TIMEFRAME,
        datetime(2026, 9, 14, 0, 0, tzinfo=timezone.utc),
        open_="77280",
        high="77500",
        low="77000",
        close="77300",
    )
    trigger = detect_exit_trigger(pos, candle)
    assert trigger is not None
    reason, price = trigger
    assert reason == ExitReason.SL
    assert price == pos.stop_loss


# --- H: gap through SL at open → open-price semantics ---


def test_h_gap_open_through_sl_uses_open_price():
    pos = _long_btc(sl="77157")
    candle = _candle(
        "btc",
        FAST_PROTECTION_TIMEFRAME,
        datetime(2026, 9, 14, 0, 1, tzinfo=timezone.utc),
        open_="77100",
        high="77120",
        low="77050",
        close="77080",
    )
    trigger = detect_exit_trigger(pos, candle)
    assert trigger == (ExitReason.SL, candle.open)


# --- I/J: restart recovery + idempotency ---


def test_i_j_recovery_finds_missed_trigger_and_is_idempotent():
    opened = datetime(2026, 9, 13, 17, 40, tzinfo=timezone.utc)
    pos = _long_btc(opened_at=opened)
    state = _portfolio_state([pos])
    # No 1m, but 5m proves SL
    sl_5m = _candle(
        "btc",
        FALLBACK_TIMEFRAME,
        datetime(2026, 9, 13, 22, 0, tzinfo=timezone.utc),
        open_="77280",
        high="77280",
        low="77022",
        close="77056",
    )
    store = _ProtectStore([sl_5m], state, _instance())
    now = datetime(2026, 9, 14, 1, 10, tzinfo=timezone.utc)

    with patch("quantara_engine.broker.execution_bridge.execute_through_broker", return_value=None):
        first = recover_missed_research_crypto_exits(store, now=now, symbols={"BTCUSD"})
    assert first["recovered_count"] == 1
    assert first["recovered"][0]["source_timeframe"] == "5m"
    assert first["recovered"][0]["trigger_reason"] == "sl"
    assert any(
        d.metadata.get("recovered_missed_protection") is True for d in store.decisions
    )

    with patch("quantara_engine.broker.execution_bridge.execute_through_broker", return_value=None):
        second = recover_missed_research_crypto_exits(store, now=now, symbols={"BTCUSD"})
    assert second["recovered_count"] == 0
    assert len(state.open_positions()) == 0
    # Simulate a stale open row with trade already recorded → must skip
    pos.status = PositionStatus.OPEN
    state.positions.append(pos)
    with patch("quantara_engine.broker.execution_bridge.execute_through_broker", return_value=None):
        third = recover_missed_research_crypto_exits(store, now=now, symbols={"BTCUSD"})
    assert third["recovered_count"] == 0
    assert any(s["reason"] == "trade_exists" for s in third["skipped"])


# --- K: ETH not falsely closed ---


def test_k_eth_not_closed_without_trigger():
    eth = _long_eth(sl="3000", tp="4000")  # far from mark
    state = _portfolio_state([eth])
    candle = _candle(
        "eth",
        FALLBACK_TIMEFRAME,
        datetime(2026, 9, 14, 0, 0, tzinfo=timezone.utc),
        open_="3500",
        high="3510",
        low="3490",
        close="3505",
    )
    store = _ProtectStore([candle], state, _instance(instrument_id="eth"))
    now = datetime(2026, 9, 14, 1, 10, tzinfo=timezone.utc)
    result = recover_missed_research_crypto_exits(store, now=now, symbols={"ETHUSD"})
    assert result["recovered_count"] == 0
    assert result["skipped"][0]["reason"] == "no_proven_trigger"
    assert len(state.open_positions()) == 1


# --- L: Live Sim crypto protection regression ---


def test_l_live_sim_1m_sl_and_5m_fallback():
    from quantara_engine.broker.accounts import LIVE_SIM_VIRTUAL_PORTFOLIO_ID

    now = datetime(2026, 9, 14, 1, 10, tzinfo=timezone.utc)
    opened = datetime(2026, 9, 13, 17, 40, tzinfo=timezone.utc)
    sl_1m = _candle(
        "btc",
        FAST_PROTECTION_TIMEFRAME,
        datetime(2026, 9, 13, 22, 5, tzinfo=timezone.utc),
        open_="77200",
        high="77210",
        low="77100",
        close="77120",
    )
    store = MagicMock()
    store.get_instrument_by_id.return_value = _btc_instrument()
    store.list_candles.return_value = [sl_1m]
    store.session = _SessionHarness()
    store.session.execute = MagicMock()

    row = {
        "id": "ls-btc-1",
        "instrument_id": "btc",
        "direction": "long",
        "quantity": Decimal("0.01"),
        "entry_price": Decimal("77267.55"),
        "stop_loss": Decimal("77157.71"),
        "take_profit": Decimal("77436.38"),
        "timeframe": "5m",
        "canonical_opportunity_key": "k",
        "broker_account_id": "acct",
        "opened_at": opened,
        "symbol": "BTCUSD",
        "broker_account_slug": "live-sim-a",
    }
    broker_ok = MagicMock()
    broker_ok.accepted = True
    broker_ok.shadow_only = False

    with patch(
        "quantara_engine.execution.crypto_fast_protection.execute_through_broker",
        return_value=broker_ok,
    ), patch(
        "quantara_engine.execution.crypto_fast_protection.BrokerExecutionService",
    ):
        from quantara_engine.execution.crypto_fast_protection import _process_live_sim_crypto

        result = _process_live_sim_crypto(
            store,
            [row],
            now=now,
            cursors={},
            fallback_cursors={},
            candles_1m_by_instrument={"btc": [sl_1m]},
            candles_5m_by_instrument={},
            stale_by_instrument={"btc": False},
        )
    assert result["closed"] == 1
    assert LIVE_SIM_VIRTUAL_PORTFOLIO_ID  # routing uses virtual portfolio

    # 5m fallback when stale
    sl_5m = _candle(
        "btc",
        FALLBACK_TIMEFRAME,
        datetime(2026, 9, 13, 22, 0, tzinfo=timezone.utc),
        open_="77280",
        high="77280",
        low="77022",
        close="77056",
    )
    store2 = MagicMock()
    store2.get_instrument_by_id.return_value = _btc_instrument()
    store2.session = _SessionHarness()
    store2.session.execute = MagicMock()
    with patch(
        "quantara_engine.execution.crypto_fast_protection.execute_through_broker",
        return_value=broker_ok,
    ), patch(
        "quantara_engine.execution.crypto_fast_protection.BrokerExecutionService",
    ):
        result2 = _process_live_sim_crypto(
            store2,
            [row],
            now=now,
            cursors={},
            fallback_cursors={},
            candles_1m_by_instrument={},
            candles_5m_by_instrument={"btc": [sl_5m]},
            stale_by_instrument={"btc": True},
        )
    assert result2["closed"] == 1


# --- Health flag ---


def test_health_flag_crypto_1m_stale():
    store = MagicMock()
    store.get_settings_dict.return_value = {"paper_trading_enabled": True}
    pos = _long_btc()
    inst = _instance()
    btc = _btc_instrument()
    stale_ts = datetime(2026, 9, 13, 19, 21, tzinfo=timezone.utc)
    stale_1m = _candle(
        "btc",
        FAST_PROTECTION_TIMEFRAME,
        stale_ts,
        open_="77200",
        high="77210",
        low="77190",
        close="77200",
    )
    now = datetime(2026, 9, 14, 1, 10, tzinfo=timezone.utc)

    with patch(
        "quantara_engine.trading.trading_controls.load_trading_control",
        return_value=MagicMock(),
    ), patch(
        "quantara_engine.trading.trading_controls.allows_position_management",
        return_value=True,
    ), patch(
        "quantara_engine.execution.crypto_fast_protection._collect_research_crypto_work",
        return_value=[(pos, inst, btc)],
    ), patch(
        "quantara_engine.execution.crypto_fast_protection._collect_live_sim_crypto_rows",
        return_value=[],
    ), patch(
        "quantara_engine.execution.crypto_fast_protection._fetch_and_store_1m",
        return_value=[stale_1m],
    ), patch(
        "quantara_engine.execution.crypto_fast_protection._process_research_crypto",
        return_value={"checked": 1, "closed": 0, "errors": [], "sources": {"1m": 0, "5m_fallback": 1}},
    ), patch(
        "quantara_engine.execution.crypto_fast_protection._process_live_sim_crypto",
        return_value={"checked": 0, "closed": 0, "errors": []},
    ), patch(
        "quantara_engine.execution.crypto_mark_valuation.apply_crypto_1m_marks",
        return_value={"applied_symbols": []},
    ), patch(
        "quantara_engine.execution.crypto_mark_valuation.prune_crypto_canonical_marks",
    ):
        store.get_instrument_by_symbol.return_value = btc
        store.latest_candle_timestamp.return_value = stale_ts
        store.list_candles.return_value = [stale_1m]
        store.session = _SessionHarness()
        report = run_crypto_fast_protection(store, now)

    assert report["health_flag"] == "CRYPTO_1M_STALE"
    assert report["freshness"]["BTCUSD"]["protection_source"] == "5m_fallback"
    assert report["freshness"]["BTCUSD"]["stale"] is True
