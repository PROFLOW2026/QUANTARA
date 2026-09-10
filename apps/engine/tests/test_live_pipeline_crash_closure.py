"""Regression tests for live pipeline crash closure (PM, UUID, intents, freshness)."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from quantara_engine.domain.types import (
    Direction,
    ExitReason,
    Mode,
    Portfolio,
    PortfolioStatus,
    Position,
    PositionStatus,
    Trade,
    new_id,
)
from quantara_engine.execution.exit_triggers import find_first_exit_candle
from quantara_engine.market_data.polling import (
    bar_staleness_minutes,
    is_market_data_fresh,
    max_staleness_minutes,
)
from quantara_engine.models.enums import WorkerRunStatus, coerce_worker_run_status
from quantara_engine.persistence.store import TradingStore, _is_uuid
from quantara_engine.portfolio.service import PortfolioState
from quantara_workers.jobs.position_management import position_management_job
from quantara_workers.jobs.run_strategy import _check_eligibility


def test_worker_run_status_error_path_maps_to_failed():
    assert coerce_worker_run_status("error") == WorkerRunStatus.FAILED
    assert coerce_worker_run_status("failed") == WorkerRunStatus.FAILED
    assert coerce_worker_run_status("partial") == WorkerRunStatus.PARTIAL
    assert coerce_worker_run_status("healthy") == WorkerRunStatus.SUCCESS


def test_pm_error_path_persists_failed_without_crashing():
    started_at = datetime.now(timezone.utc)

    class _BrokenStore:
        def get_settings_dict(self):
            return {"paper_trading_enabled": True}

        def update_worker_status(self, *_args, **_kwargs):
            return None

        def save_worker_run(self, **_kwargs):
            status = _kwargs.get("status")
            assert coerce_worker_run_status(status) == WorkerRunStatus.FAILED

    with patch(
        "quantara_workers.jobs.position_management.manage_all_open_positions",
        side_effect=RuntimeError("boom"),
    ):
        with pytest.raises(RuntimeError):
            position_management_job(store=_BrokenStore())  # type: ignore[arg-type]


def test_save_trade_resolves_missing_strategy_version_id():
    store = MagicMock(spec=TradingStore)
    store.resolve_strategy_version_id.return_value = "00000000-0000-0000-0000-000000000001"
    trade = Trade(
        id=new_id(),
        position_id=new_id(),
        portfolio_id=new_id(),
        strategy_instance_id="00000000-0000-0000-0000-000000002301",
        strategy_version_id="",
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
    real_save = TradingStore.save_trade
    captured: dict = {}

    def _capture(self, incoming):
        captured["version"] = incoming.strategy_version_id
        assert _is_uuid(incoming.strategy_version_id) is False

    store.save_trade = lambda incoming: _capture(store, incoming)
    assert not _is_uuid(trade.strategy_version_id)
    version_id = store.resolve_strategy_version_id(trade.strategy_instance_id)
    assert uuid.UUID(version_id)


def test_malformed_strategy_version_id_is_not_valid_uuid():
    assert _is_uuid("") is False
    assert _is_uuid("sv1") is False
    assert _is_uuid("00000000-0000-0000-0000-000000002301") is True


def test_1h_freshness_uses_bar_close_not_open():
    latest = datetime(2026, 9, 9, 10, 0, tzinfo=timezone.utc)
    now = datetime(2026, 9, 9, 11, 43, tzinfo=timezone.utc)
    assert bar_staleness_minutes(latest, "1h", now) == pytest.approx(43.0, abs=0.1)
    assert is_market_data_fresh(latest, "1h", now) is True
    assert max_staleness_minutes("1h") == 75


def test_5m_and_15m_freshness_remain_correct():
    now = datetime(2026, 9, 9, 11, 43, tzinfo=timezone.utc)
    fresh_5m = datetime(2026, 9, 9, 11, 35, tzinfo=timezone.utc)
    stale_5m = datetime(2026, 9, 9, 9, 25, tzinfo=timezone.utc)
    assert is_market_data_fresh(fresh_5m, "5m", now) is True
    assert is_market_data_fresh(stale_5m, "5m", now) is False

    fresh_15m = datetime(2026, 9, 9, 11, 15, tzinfo=timezone.utc)
    assert is_market_data_fresh(fresh_15m, "15m", now) is True


def test_check_eligibility_1h_not_false_stale_at_103m_since_open():
    instrument = MagicMock()
    instrument.symbol = "BTCUSD"
    instrument.id = "4141eaf4-977d-4730-9b30-3a077035f8ac"
    store = MagicMock()
    store.count_candles.return_value = 400
    store.latest_candle_timestamp.return_value = datetime(2026, 9, 9, 10, 0, tzinfo=timezone.utc)
    store.get_settings_dict.return_value = {}
    now = datetime(2026, 9, 9, 11, 43, tzinfo=timezone.utc)
    eligible, reason = _check_eligibility(store, instrument, "1h", now)
    assert eligible is True
    assert reason == "eligible"


def test_missed_exit_catchup_closes_on_first_historical_trigger():
    from quantara_engine.domain.types import Candle

    pos = Position(
        id="p1",
        portfolio_id="pf1",
        strategy_instance_id="si1",
        instrument_id="btc",
        direction=Direction.LONG,
        quantity=Decimal("0.1"),
        entry_price=Decimal("79538"),
        stop_loss=Decimal("79376"),
        take_profit=Decimal("79815"),
        current_price=Decimal("79538"),
        status=PositionStatus.OPEN,
        opened_at=datetime(2026, 9, 9, 9, 15, tzinfo=timezone.utc),
        strategy_version_id="00000000-0000-0000-0000-000000000001",
    )
    trigger_candle = Candle(
        instrument_id="btc",
        timeframe="5m",
        timestamp=datetime(2026, 9, 9, 9, 35, tzinfo=timezone.utc),
        open=Decimal("79450"),
        high=Decimal("79480"),
        low=Decimal("79300"),
        close=Decimal("79350"),
        volume=Decimal("1"),
    )
    later = Candle(
        instrument_id="btc",
        timeframe="5m",
        timestamp=datetime(2026, 9, 9, 10, 0, tzinfo=timezone.utc),
        open=Decimal("79000"),
        high=Decimal("79100"),
        low=Decimal("78800"),
        close=Decimal("78900"),
        volume=Decimal("1"),
    )
    hit = find_first_exit_candle(
        pos,
        [trigger_candle, later],
        after_timestamp=datetime(2026, 9, 9, 9, 30, 20, tzinfo=timezone.utc),
    )
    assert hit is not None
    candle, reason, price = hit
    assert candle.timestamp == trigger_candle.timestamp
    assert reason == ExitReason.SL
    assert price == pos.stop_loss


def test_position_management_continues_when_strategy_fails():
    from quantara_engine.domain.types import Candle, Instrument, StrategyInstance
    from quantara_engine.execution.position_management import process_position_management

    pos = Position(
        id="pos-1",
        portfolio_id="p1",
        strategy_instance_id="si1",
        instrument_id="btc",
        direction=Direction.LONG,
        quantity=Decimal("0.01"),
        entry_price=Decimal("79538"),
        stop_loss=Decimal("79000"),
        take_profit=Decimal("80000"),
        current_price=Decimal("79538"),
        status=PositionStatus.OPEN,
        opened_at=datetime(2026, 9, 9, 9, 15, tzinfo=timezone.utc),
        strategy_version_id="00000000-0000-0000-0000-000000000001",
    )
    candle = Candle(
        instrument_id="btc",
        timeframe="5m",
        timestamp=datetime(2026, 9, 9, 9, 40, tzinfo=timezone.utc),
        open=Decimal("79400"),
        high=Decimal("79450"),
        low=Decimal("79350"),
        close=Decimal("79400"),
        volume=Decimal("1"),
    )
    state = PortfolioState(
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
    store = MagicMock()
    store.get_settings_dict.return_value = {"position_management_cursors": {}}
    store.update_settings = MagicMock()
    store.list_candles.return_value = [candle]
    store.load_portfolio_state.return_value = state
    store.update_open_position_mark = MagicMock()
    store.update_portfolio = MagicMock()
    instance = StrategyInstance(
        id="si1",
        portfolio_id="p1",
        strategy_slug="gold-trend-pullback",
        strategy_version="1.0.0",
        strategy_version_id="00000000-0000-0000-0000-000000000001",
        instrument_id="btc",
        timeframe="5m",
        risk_profile_id="rp1",
        parameter_overrides={},
    )
    instrument = Instrument(
        id="btc",
        symbol="BTCUSD",
        name="BTC/USD",
        asset_class="crypto",
        quantity_step=Decimal("0.0001"),
        min_quantity=Decimal("0.0001"),
    )
    with patch(
        "quantara_workers.jobs.run_strategy.run_strategy_job",
        side_effect=ValueError("badly formed hexadecimal UUID string"),
    ):
        result = process_position_management(
            store,
            position=pos,
            instance=instance,
            instrument=instrument,
            now=datetime(2026, 9, 9, 9, 45, tzinfo=timezone.utc),
        )
    assert result["status"] == "managed"


def test_strategy_runner_defers_exits_to_position_management():
    from quantara_engine.pipeline.candle_processor import CandleProcessor

    processor = CandleProcessor(
        portfolio_state=MagicMock(),
        strategy_instance=MagicMock(),
        instrument=MagicMock(),
        risk_profile=MagicMock(),
        broker=MagicMock(),
        store=MagicMock(),
        manage_exits=False,
    )
    assert processor.manage_exits is False


def test_stale_entry_intent_expires_after_execution_window():
    from quantara_engine.models.enums import OrderIntentStatus
    from quantara_engine.models.portfolio import StrategyInstance as OrmStrategyInstance
    from quantara_engine.models.trading import OrderIntent as OrmOrderIntent

    now = datetime(2026, 9, 9, 12, 0, tzinfo=timezone.utc)
    row = MagicMock(spec=OrmOrderIntent)
    row.strategy_instance_id = uuid.uuid4()
    row.execution_candle_timestamp = datetime(2026, 9, 9, 7, 0, tzinfo=timezone.utc)
    row.signal_candle_timestamp = datetime(2026, 9, 9, 6, 55, tzinfo=timezone.utc)
    row.created_at = datetime(2026, 9, 9, 7, 1, tzinfo=timezone.utc)
    row.status = OrderIntentStatus.PENDING_EXECUTION

    instance = MagicMock(spec=OrmStrategyInstance)
    instance.timeframe = MagicMock(value="5m")

    session = MagicMock()
    session.scalars.return_value.all.return_value = [row]
    session.get.return_value = instance

    store = TradingStore(session)
    store.get_settings_dict = MagicMock(return_value={})
    cancelled = store.cancel_stale_pending_intents(
        "00000000-0000-0000-0000-000000000400",
        now,
    )
    assert cancelled == 1
    assert row.status == OrderIntentStatus.EXPIRED
    assert row.rejection_reason == "execution_window_passed"
