"""Scheduler-offset execution window tests — run_strategy +18s, execute_intents +32s."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from quantara_engine.domain.types import (
    Candle,
    Direction,
    IntentStatus,
    OrderIntent,
    Portfolio,
    PortfolioStatus,
    RiskProfile,
    StrategyInstance,
    new_id,
)
from quantara_engine.execution.cost_profile import execution_assumptions_for
from quantara_engine.execution.live_intents import execute_pending_intents_live
from quantara_engine.execution.paper_broker import PaperBrokerAdapter
from quantara_engine.execution.timing import (
    intent_past_execution_window,
    live_fill_allowed,
)
from quantara_engine.market_data.polling import is_bar_complete
from quantara_engine.persistence.store import TradingStore


TZ3 = timezone(timedelta(hours=3))


def _btc():
    from quantara_engine.domain.types import Instrument
    from decimal import Decimal

    return Instrument(
        id="btc-id",
        symbol="BTCUSD",
        name="BTC/USD",
        asset_class="crypto",
        quote_currency="USD",
        quantity_step=Decimal("0.0001"),
        min_quantity=Decimal("0.0001"),
    )


@pytest.mark.parametrize(
    ("timeframe", "signal_ts", "exec_ts"),
    [
        ("5m", datetime(2026, 9, 12, 22, 55, tzinfo=TZ3), datetime(2026, 9, 12, 23, 0, tzinfo=TZ3)),
        ("15m", datetime(2026, 9, 12, 22, 45, tzinfo=TZ3), datetime(2026, 9, 12, 23, 0, tzinfo=TZ3)),
        ("1h", datetime(2026, 9, 12, 22, 0, tzinfo=TZ3), datetime(2026, 9, 12, 23, 0, tzinfo=TZ3)),
    ],
)
def test_scheduler_offset_intent_not_expired_at_run_and_execute(timeframe, signal_ts, exec_ts):
    """Signal bar closes → run_strategy :18 → execute_intents :32 must stay pending."""
    created = datetime(2026, 9, 12, 23, 0, 18, tzinfo=TZ3)
    execute_at = datetime(2026, 9, 12, 23, 0, 32, tzinfo=TZ3)

    assert not intent_past_execution_window(
        signal_candle_timestamp=signal_ts,
        execution_candle_timestamp=exec_ts,
        intent_created_at=created,
        timeframe=timeframe,
        now=execute_at,
    )
    allowed, reason = live_fill_allowed(
        execution_candle_timestamp=exec_ts,
        candle_timestamp=exec_ts,
        signal_candle_timestamp=signal_ts,
        now=execute_at,
        timeframe=timeframe,
    )
    assert allowed is False
    assert reason == "execution_bar_not_complete"
    assert not is_bar_complete(exec_ts, timeframe, execute_at)


def test_pending_stays_until_execution_bar_complete():
    signal_ts = datetime(2026, 9, 12, 22, 55, tzinfo=timezone.utc)
    exec_ts = datetime(2026, 9, 12, 23, 0, tzinfo=timezone.utc)
    created = datetime(2026, 9, 12, 23, 0, 18, tzinfo=timezone.utc)

    for minute in (0, 1, 2, 3, 4):
        now = datetime(2026, 9, 12, 23, minute, 32, tzinfo=timezone.utc)
        assert not intent_past_execution_window(
            signal_candle_timestamp=signal_ts,
            execution_candle_timestamp=exec_ts,
            intent_created_at=created,
            timeframe="5m",
            now=now,
        )


def test_true_expiry_after_grace_once_bar_closed():
    signal_ts = datetime(2026, 9, 12, 22, 55, tzinfo=timezone.utc)
    exec_ts = datetime(2026, 9, 12, 23, 0, tzinfo=timezone.utc)
    created = datetime(2026, 9, 12, 23, 0, 18, tzinfo=timezone.utc)
    now = datetime(2026, 9, 12, 23, 14, tzinfo=timezone.utc)

    assert intent_past_execution_window(
        signal_candle_timestamp=signal_ts,
        execution_candle_timestamp=exec_ts,
        intent_created_at=created,
        timeframe="5m",
        now=now,
    )


def test_save_order_intent_stamps_paper_run_after_flush():
    from quantara_engine.models.enums import OrderIntentStatus as OrmOrderIntentStatus
    from quantara_engine.models.trading import OrderIntent as OrmOrderIntent

    flush_calls: list[str] = []
    session = MagicMock()

    def _track_flush():
        flush_calls.append("flush")

    session.flush.side_effect = _track_flush

    from quantara_engine.domain.types import Mode

    store = TradingStore(session)
    store.mode = Mode.PAPER
    store.get_settings_dict = MagicMock(return_value={"current_paper_run_id": "run-1"})
    store._requires_canonical_opportunity_key = MagicMock(return_value=False)
    store.session.scalar.return_value = None
    store._bt_uuid = MagicMock(return_value=None)

    intent = OrderIntent(
        id=new_id(),
        signal_id=new_id(),
        strategy_instance_id=new_id(),
        portfolio_id=new_id(),
        direction=Direction.LONG,
        quantity=1,
        stop_loss=1,
        take_profit=None,
        target_risk_amount=10,
        actual_risk_amount=10,
        signal_candle_timestamp=datetime(2026, 9, 12, 22, 55, tzinfo=timezone.utc),
        execution_candle_timestamp=datetime(2026, 9, 12, 23, 0, tzinfo=timezone.utc),
        risk_profile_id=new_id(),
        status=IntentStatus.PENDING_EXECUTION,
    )

    with patch(
        "quantara_engine.competition.paper_run.stamp_paper_run_id"
    ) as stamp:
        store.save_order_intent(intent)
        assert flush_calls == ["flush"]
        stamp.assert_called_once_with(
            store, table="order_intents", row_id=intent.id
        )


def test_execute_intents_sees_pending_intent_with_paper_run_scope():
    from decimal import Decimal

    from quantara_engine.portfolio.currency import FxRateTable
    from quantara_engine.portfolio.service import PortfolioState

    signal_ts = datetime(2026, 9, 12, 22, 55, tzinfo=timezone.utc)
    exec_ts = datetime(2026, 9, 12, 23, 0, tzinfo=timezone.utc)
    now = datetime(2026, 9, 12, 23, 5, 32, tzinfo=timezone.utc)
    candle = Candle(
        instrument_id="btc-id",
        timeframe="5m",
        timestamp=exec_ts,
        open=Decimal("77000"),
        high=Decimal("77200"),
        low=Decimal("76900"),
        close=Decimal("77100"),
        volume=Decimal("1"),
    )
    portfolio = Portfolio(
        id="p1",
        name="BTC 5m",
        mode="paper",
        initial_capital=Decimal("2000"),
        balance=Decimal("2000"),
        equity=Decimal("2000"),
        status=PortfolioStatus.ACTIVE,
    )
    instance = StrategyInstance(
        id="si1",
        portfolio_id="p1",
        strategy_slug="gold-trend-pullback",
        strategy_version="1.0.0",
        strategy_version_id="sv1",
        instrument_id="btc-id",
        timeframe="5m",
        risk_profile_id="rp1",
        parameter_overrides={},
    )
    intent = OrderIntent(
        id=new_id(),
        signal_id=new_id(),
        strategy_instance_id=instance.id,
        portfolio_id=portfolio.id,
        direction=Direction.LONG,
        quantity=Decimal("0.01"),
        stop_loss=Decimal("76000"),
        take_profit=Decimal("78000"),
        target_risk_amount=Decimal("20"),
        actual_risk_amount=Decimal("20"),
        signal_candle_timestamp=signal_ts,
        execution_candle_timestamp=exec_ts,
        risk_profile_id="rp1",
        status=IntentStatus.PENDING_EXECUTION,
    )
    instrument = _btc()
    risk = RiskProfile(
        id="rp1",
        slug="balanced",
        name="Balanced",
        risk_per_trade_pct=Decimal("1"),
        max_open_positions=5,
        max_total_exposure_pct=Decimal("500"),
        daily_loss_limit_pct=Decimal("10"),
        max_drawdown_pct=Decimal("50"),
    )
    store = MagicMock()
    store.list_all_competition_entries.return_value = (
        [],
        [],
        [{"portfolio": portfolio, "instance": instance, "risk_profile": risk}],
    )
    store.get_instrument_by_id.return_value = instrument
    store.list_recent_candles.return_value = [candle]
    store.list_pending_order_intents.return_value = [intent]
    store.load_portfolio_state.return_value = PortfolioState(portfolio=portfolio)
    store.build_currency_context_for_instruments.return_value = MagicMock(
        fx_rates=FxRateTable.usd_only()
    )
    store.get_settings_dict.return_value = {
        "trading_control_state": {"state": "running"},
        "paper_trading_enabled": True,
    }
    store.cancel_stale_pending_intents.return_value = 0
    store.session.commit = MagicMock()
    store.resolve_entry_opportunity_key.return_value = "opp:test"
    store.save_order = MagicMock()
    store.save_order_intent = MagicMock(side_effect=lambda i, **k: i)
    store.update_order_intent_status = MagicMock()
    store.sync_portfolios_financial_state_from_ledger = MagicMock()
    store.flush = MagicMock()

    with patch("quantara_engine.broker.execution_bridge.execute_through_broker") as broker_exec:
        broker_exec.return_value = MagicMock(
            accepted=True,
            broker_order_id="ord-1",
            broker_fill_id="fill-1",
            physical_opened_qty=intent.quantity,
            from_existing_fill=False,
            shadow_only=False,
            decision=None,
        )
        report = execute_pending_intents_live(store, now)

    assert report["portfolios_checked"] == 1
    assert report["fills_attempted"] >= 1
    store.list_pending_order_intents.assert_called()


def test_cancel_stale_does_not_expire_before_execution_bar_closes():
    from quantara_engine.models.enums import OrderIntentStatus
    from quantara_engine.models.portfolio import StrategyInstance as OrmStrategyInstance
    from quantara_engine.models.trading import OrderIntent as OrmOrderIntent

    now = datetime(2026, 9, 12, 23, 0, 32, tzinfo=TZ3)
    row = MagicMock(spec=OrmOrderIntent)
    row.strategy_instance_id = uuid.uuid4()
    row.execution_candle_timestamp = datetime(2026, 9, 12, 23, 0, tzinfo=TZ3)
    row.signal_candle_timestamp = datetime(2026, 9, 12, 22, 55, tzinfo=TZ3)
    row.created_at = datetime(2026, 9, 12, 23, 0, 18, tzinfo=TZ3)
    row.status = OrderIntentStatus.PENDING_EXECUTION

    instance = MagicMock(spec=OrmStrategyInstance)
    instance.timeframe = MagicMock(value="5m")

    session = MagicMock()
    session.scalars.return_value.all.return_value = [row]
    session.get.return_value = instance

    store = TradingStore(session)
    cancelled = store.cancel_stale_pending_intents(
        "00000000-0000-0000-0000-000000000400",
        now,
    )
    assert cancelled == 0
    assert row.status == OrderIntentStatus.PENDING_EXECUTION
