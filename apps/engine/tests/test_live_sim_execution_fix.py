"""Live Sim execution fix — sizing headroom, pending resume, robot routing."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from quantara_engine.broker.account import build_account_snapshot
from quantara_engine.broker.pre_trade import evaluate_broker_order
from quantara_engine.broker.profile import QUANTARA_LIVE_SIM_10K
from quantara_engine.broker.types import BrokerOrderRequest, BrokerRejectionReason
from quantara_engine.competition.robot_registry import MULTI_STRATEGY_SLUGS, ROBOT_LABELS
from quantara_engine.domain.types import Candle, Direction, Instrument, SignalAction
from quantara_engine.execution.cost_profile import execution_assumptions_for
from quantara_engine.execution.timing import is_execution_candle_ready, intent_past_execution_window
from quantara_engine.live_sim.allocator import resume_all_pending_live_sim_allocations
from quantara_engine.live_sim.opportunity import live_sim_canonical_opportunity_key
from quantara_engine.live_sim.sizing import (
    broker_quantized_asset_leverage,
    entry_mark_price_for_sizing,
    size_live_sim_entry,
)
from quantara_engine.portfolio.currency import FxRateTable

TZ3 = timezone(timedelta(hours=3))


def _eth() -> Instrument:
    return Instrument(
        id="eth-id",
        symbol="ETHUSD",
        name="ETH/USD",
        asset_class="crypto",
        quote_currency="USD",
        quantity_step=Decimal("0.0001"),
        min_quantity=Decimal("0.0001"),
    )


def _candle(ts: datetime, close: str = "2525") -> Candle:
    return Candle(
        instrument_id="eth-id",
        timeframe="15m",
        timestamp=ts,
        open=Decimal(close),
        high=Decimal(close),
        low=Decimal(close),
        close=Decimal(close),
        volume=Decimal("1"),
    )


def test_eth_10k_buying_power_headroom_broker_accepts():
    """Audit case: ~3.96 ETH at full cash hits max_asset_exposure; safe qty passes broker."""
    eth = _eth()
    equity = cash = Decimal("10000")
    entry = Decimal("2525")
    sl = Decimal("2500")
    assumptions = execution_assumptions_for(eth, entry)
    mark = entry_mark_price_for_sizing(entry, Direction.LONG, assumptions)
    fx = FxRateTable.usd_only()

    old_qty = Decimal("3.9604")
    old_notional = old_qty * mark
    old_lev = broker_quantized_asset_leverage(old_notional, equity)
    account = build_account_snapshot(
        cash=cash,
        balance=cash,
        realized_pnl=Decimal("0"),
        positions={},
        fx_rates={"USD": Decimal("1")},
    )
    old_dec = evaluate_broker_order(
        account,
        QUANTARA_LIVE_SIM_10K,
        BrokerOrderRequest(
            symbol="ETHUSD",
            asset_class="crypto",
            direction="long",
            quantity=old_qty,
            mark_price=mark,
            data_fresh=True,
            market_open=True,
        ),
        {"USD": Decimal("1")},
    )

    sizing = size_live_sim_entry(
        equity=equity,
        cash=cash,
        target_risk=Decimal("100"),
        entry_reference=entry,
        stop_loss=sl,
        direction=Direction.LONG,
        instrument=eth,
        fx_rates=fx,
        execution_assumptions=assumptions,
        max_asset_leverage=Decimal("1"),
    )
    new_notional = sizing.quantity * mark
    new_lev = broker_quantized_asset_leverage(new_notional, equity)
    new_dec = evaluate_broker_order(
        account,
        QUANTARA_LIVE_SIM_10K,
        BrokerOrderRequest(
            symbol="ETHUSD",
            asset_class="crypto",
            direction="long",
            quantity=sizing.quantity,
            mark_price=mark,
            data_fresh=True,
            market_open=True,
        ),
        {"USD": Decimal("1")},
    )

    assert old_lev > Decimal("1")
    assert not old_dec.accepted
    assert old_dec.rejection_reason == BrokerRejectionReason.MAX_ASSET_EXPOSURE
    assert sizing.sizing_reason == "buying_power_cap"
    assert new_lev <= Decimal("1")
    assert new_dec.accepted
    assert sizing.quantity < old_qty
    assert sizing.headroom_notional_usd is not None
    assert sizing.headroom_notional_usd >= Decimal("0")


def test_spread_headroom_prevents_rejection_when_raw_max_over_limit():
    eth = _eth()
    equity = cash = Decimal("10000")
    entry = Decimal("2525")
    assumptions = execution_assumptions_for(eth, entry)
    mark = entry_mark_price_for_sizing(entry, Direction.LONG, assumptions)

    raw_max_qty = (cash / entry).quantize(Decimal("0.0001"))
    raw_lev = broker_quantized_asset_leverage(raw_max_qty * mark, equity)
    assert raw_lev > Decimal("1")

    sizing = size_live_sim_entry(
        equity=equity,
        cash=cash,
        target_risk=Decimal("100"),
        entry_reference=entry,
        stop_loss=Decimal("2500"),
        direction=Direction.LONG,
        instrument=eth,
        fx_rates=FxRateTable.usd_only(),
        execution_assumptions=assumptions,
        max_asset_leverage=Decimal("1"),
    )
    safe_lev = broker_quantized_asset_leverage(sizing.quantity * mark, equity)
    assert safe_lev <= Decimal("1")
    assert sizing.quantity <= raw_max_qty


def test_actual_risk_truthful_after_buying_power_cap():
    eth = _eth()
    sizing = size_live_sim_entry(
        equity=Decimal("10000"),
        cash=Decimal("10000"),
        target_risk=Decimal("100"),
        entry_reference=Decimal("2525"),
        stop_loss=Decimal("2500"),
        direction=Direction.LONG,
        instrument=eth,
        fx_rates=FxRateTable.usd_only(),
        execution_assumptions=execution_assumptions_for(eth, Decimal("2525")),
        max_asset_leverage=Decimal("1"),
    )
    assert sizing.sizing_reason == "buying_power_cap"
    assert sizing.expected_risk_usd > sizing.target_risk_usd
    assert sizing.deny_reason is None


def test_pending_allocation_counters_when_not_ready():
    store = MagicMock()
    pending_row = {
        "id": "log-1",
        "canonical_opportunity_key": "canon-1",
        "strategy_slug": "gold-trend-pullback",
        "symbol": "ETHUSD",
        "timeframe": "15m",
    }
    store.session.execute.return_value.mappings.return_value.all.return_value = [pending_row]

    signal_ts = datetime(2026, 9, 13, 0, 0, tzinfo=TZ3)
    exec_ts = datetime(2026, 9, 13, 0, 15, tzinfo=TZ3)
    before_close = datetime(2026, 9, 13, 0, 29, tzinfo=TZ3)

    with patch(
        "quantara_engine.live_sim.allocator._account_row",
        return_value={"id": "acct", "is_active": True, "equity": 10000, "starting_cash": 10000},
    ), patch(
        "quantara_engine.live_sim.allocator.expire_stale_live_sim_allocations",
        return_value=0,
    ), patch(
        "quantara_engine.live_sim.allocator.find_allocation_by_canonical",
        return_value={
            "id": "log-1",
            "canonical_opportunity_key": "canon-1",
            "strategy_slug": "gold-trend-pullback",
            "strategy_version": "1.0.0",
            "robot_label": "Robot A",
            "symbol": "ETHUSD",
            "timeframe": "15m",
            "direction": "long",
            "signal_candle_timestamp": signal_ts,
            "proposed_entry": Decimal("2525"),
            "stop_loss": Decimal("2500"),
            "take_profit": Decimal("2600"),
            "calculated_quantity": Decimal("1"),
            "calculated_risk_usd": Decimal("100"),
            "metadata": {
                "pending_execution": True,
                "execution_candle_timestamp": exec_ts.isoformat(),
            },
        },
    ), patch(
        "quantara_engine.live_sim.allocator.resolve_execution_candle",
        return_value=(_candle(exec_ts), exec_ts),
    ):
        report = resume_all_pending_live_sim_allocations(store, before_close)

    assert report["pending_found"] == 1
    assert report["not_ready"] == 1
    assert report["resumed"] == 0


def test_expire_uses_stored_execution_candle_timestamp_not_signal_only():
    from quantara_engine.live_sim.candidate_log import expire_stale_live_sim_allocations

    signal_ts = datetime(2026, 9, 13, 0, 0, tzinfo=TZ3)
    exec_ts = datetime(2026, 9, 13, 0, 15, tzinfo=TZ3)
    created_at = datetime(2026, 9, 13, 0, 1, tzinfo=TZ3)
    before_window = datetime(2026, 9, 13, 0, 29, tzinfo=TZ3)

    store = MagicMock()
    store.session.execute.return_value.mappings.return_value.all.return_value = [
        {
            "id": "log-1",
            "signal_candle_timestamp": signal_ts,
            "timeframe": "15m",
            "created_at": created_at,
            "metadata": {"execution_candle_timestamp": exec_ts.isoformat(), "pending_execution": True},
        }
    ]

    with patch("quantara_engine.live_sim.candidate_log.mark_allocation_expired") as mark_exp:
        expired = expire_stale_live_sim_allocations(store, before_window)

    assert expired == 0
    mark_exp.assert_not_called()
    assert not intent_past_execution_window(
        signal_candle_timestamp=signal_ts,
        execution_candle_timestamp=exec_ts,
        intent_created_at=created_at,
        timeframe="15m",
        now=before_window,
    )


def test_true_stale_expiry_still_works():
    from quantara_engine.live_sim.candidate_log import expire_stale_live_sim_allocations

    signal_ts = datetime(2026, 9, 13, 0, 0, tzinfo=TZ3)
    exec_ts = datetime(2026, 9, 13, 0, 15, tzinfo=TZ3)
    created_at = datetime(2026, 9, 13, 0, 1, tzinfo=TZ3)
    stale_now = datetime(2026, 9, 13, 0, 54, tzinfo=TZ3)

    store = MagicMock()
    store.session.execute.return_value.mappings.return_value.all.return_value = [
        {
            "id": "log-1",
            "signal_candle_timestamp": signal_ts,
            "timeframe": "15m",
            "created_at": created_at,
            "metadata": {"execution_candle_timestamp": exec_ts.isoformat(), "pending_execution": True},
        }
    ]

    with patch("quantara_engine.live_sim.candidate_log.mark_allocation_expired") as mark_exp:
        expired = expire_stale_live_sim_allocations(store, stale_now)

    assert expired == 1
    mark_exp.assert_called_once()


def test_canonical_key_one_live_sim_per_logical_opportunity():
    base = {
        "strategy_slug": "momentum-continuation",
        "strategy_version": "1.0.0",
        "symbol": "ETHUSD",
        "timeframe": "15m",
        "direction": "long",
        "opportunity_key": "pullback:ETHUSD:15m:long:2026-09-13T00:00:00",
    }
    keys = {live_sim_canonical_opportunity_key(**base) for _ in range(5)}
    assert len(keys) == 1


@pytest.mark.parametrize(
    "strategy_slug,label",
    [
        ("gold-trend-pullback", "Robot A"),
        ("opening-range-breakout", "Robot B"),
        ("mean-reversion", "Robot C"),
        ("volatility-squeeze", "Robot D"),
        ("momentum-continuation", "Robot E"),
    ],
)
def test_all_robots_have_live_sim_routing_labels(strategy_slug, label):
    assert ROBOT_LABELS[strategy_slug] == label


def test_per_portfolio_eval_routes_live_sim_once_for_cde_robots():
    from quantara_workers.jobs.run_strategy import _process_candle_batch

    store = MagicMock()
    instrument = MagicMock(symbol="ETHUSD", id="inst-eth")
    candle_ts = datetime(2026, 9, 13, 0, 0, tzinfo=TZ3)
    exec_ts = datetime(2026, 9, 13, 0, 15, tzinfo=TZ3)
    candles = [
        _candle(candle_ts),
        _candle(exec_ts),
    ]
    buy_signal = MagicMock(action=SignalAction.BUY, suggested_sl=Decimal("2500"), suggested_tp=Decimal("2600"))

    group = []
    for slug in sorted(MULTI_STRATEGY_SLUGS):
        group.append(
            {
                "portfolio": MagicMock(id=f"p-{slug}"),
                "instance": MagicMock(
                    id=f"i-{slug}",
                    strategy_slug=slug,
                    instrument_id="inst-eth",
                    strategy_version_id="sv",
                    timeframe="15m",
                    parameter_overrides={},
                ),
                "risk_profile": MagicMock(),
            }
        )

    store.timeframe_group_already_processed.return_value = False
    store.batch_load_portfolio_states.return_value = {}
    store.load_portfolio_runtime_state.return_value = MagicMock(open_positions=MagicMock(return_value=[]))
    store.list_pending_order_intents.return_value = []

    live_sim_eval_calls = {"count": 0}
    per_portfolio_eval_calls = {"count": 0}

    with patch("quantara_workers.jobs.run_strategy.CandleProcessor") as CP, patch(
        "quantara_engine.live_sim.allocator.maybe_allocate_live_sim",
        return_value={"status": "queued"},
    ) as allocate:
        def make_processor(*args, **kwargs):
            proc = MagicMock()
            proc.all_candles = []
            proc.decisions = []
            proc.pending_intents = []
            proc._persisted_intents = set()
            proc.evaluate_signal.return_value = (buy_signal, candles[0])
            proc.process_candle.return_value = MagicMock(decisions=[], pending_intents=[])
            return proc

        def side_effect(*args, **kwargs):
            proc = make_processor()
            if live_sim_eval_calls["count"] == 0:
                live_sim_eval_calls["count"] += 1
            else:
                per_portfolio_eval_calls["count"] += 1
            return proc

        CP.side_effect = side_effect
        _process_candle_batch(
            store,
            instrument,
            "15m",
            group,
            candles,
            0,
            datetime(2026, 9, 13, 0, 30, 32, tzinfo=TZ3),
            MagicMock(),
            exec_ts,
            allow_live_execution=True,
            per_portfolio_eval=True,
        )

    allocate.assert_called_once()
    assert live_sim_eval_calls["count"] == 1
    assert per_portfolio_eval_calls["count"] == len(MULTI_STRATEGY_SLUGS)


def test_execute_intents_reports_live_sim_resume_counters():
    from quantara_engine.execution.live_intents import execute_pending_intents_live

    store = MagicMock()
    store.get_settings_dict.return_value = {"trading_control": {}}
    store.list_all_competition_entries.return_value = ([], [], [])
    store.session.commit = MagicMock()

    resume_report = {
        "pending_found": 2,
        "not_ready": 1,
        "resumed": 1,
        "expired": 0,
        "broker_rejected": 0,
        "filled": 1,
    }

    with patch(
        "quantara_engine.trading.trading_controls.allows_new_entries",
        return_value=True,
    ), patch(
        "quantara_engine.execution.live_intents._expire_stale_intents",
        return_value=0,
    ), patch(
        "quantara_engine.execution.live_intents._collect_pending_work",
        return_value=[],
    ), patch(
        "quantara_engine.live_sim.allocator.resume_all_pending_live_sim_allocations",
        return_value=resume_report,
    ):
        report = execute_pending_intents_live(store, datetime.now(timezone.utc))

    assert report["live_sim_pending_found"] == 2
    assert report["live_sim_not_ready"] == 1
    assert report["live_sim_resumed"] == 1
    assert report["live_sim_filled"] == 1


def test_crypto_fast_protection_module_preserved():
    from quantara_engine.execution.crypto_fast_protection import (
        FAST_PROTECTION_IDEMPOTENCY_PREFIX,
        is_fast_protection_crypto,
        run_crypto_fast_protection,
    )

    assert FAST_PROTECTION_IDEMPOTENCY_PREFIX == "pm1m"
    assert is_fast_protection_crypto("BTCUSD") is True
    assert is_fast_protection_crypto("ETHUSD") is True
    assert callable(run_crypto_fast_protection)


def test_eth_pending_executable_after_execution_bar_close():
    signal_ts = datetime(2026, 9, 13, 0, 0, tzinfo=TZ3)
    exec_ts = datetime(2026, 9, 13, 0, 15, tzinfo=TZ3)
    after = datetime(2026, 9, 13, 0, 30, 32, tzinfo=TZ3)
    allowed, reason = is_execution_candle_ready(
        signal_candle_timestamp=signal_ts,
        execution_candle_timestamp=exec_ts,
        timeframe="15m",
        now=after,
    )
    assert allowed is True
    assert reason is None
