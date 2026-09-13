"""Tests for 1-minute US equity + FX position protection."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from contextlib import ExitStack
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
from quantara_engine.execution.crypto_mark_valuation import (
    FAST_CANONICAL_MARKS_KEY,
    apply_fast_1m_marks,
    fast_mark_owned_by_1m,
    is_fast_1m_protected_symbol,
    is_fast_protection_equity,
    is_fast_protection_fx,
)
from quantara_engine.execution.fx_fast_credit_guard import (
    INTERNAL_GUARD_LIMIT,
    SCHEDULED_INGEST_RESERVE,
    can_run_fast_fx_fetch,
    remaining_fast_fx_budget,
    select_fx_symbols_this_cycle,
)
from quantara_engine.execution.non_crypto_fast_protection import (
    is_non_crypto_fast_protection,
    run_non_crypto_fast_protection,
)
from quantara_engine.execution.position_management import manage_all_open_positions, process_position_management
from quantara_engine.live_sim.position_management import process_live_sim_exits
from quantara_engine.market_data.polling import FAST_PROTECTION_TIMEFRAME, PROVIDER_TIMEFRAME
from quantara_engine.portfolio.service import PortfolioState

TZ = timezone.utc
RTH_OPEN = datetime(2026, 9, 14, 14, 30, tzinfo=TZ)  # 10:30 ET Monday


def _candle_1m(ts: datetime, *, high: str, low: str = "100", close: str = "101", iid: str = "nvda") -> Candle:
    return Candle(
        instrument_id=iid,
        timeframe=FAST_PROTECTION_TIMEFRAME,
        timestamp=ts,
        open=Decimal("100"),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
        volume=Decimal("1"),
    )


def _candle_5m(ts: datetime, close: str, iid: str = "nvda") -> Candle:
    return Candle(
        instrument_id=iid,
        timeframe=PROVIDER_TIMEFRAME,
        timestamp=ts,
        open=Decimal(close),
        high=Decimal(close),
        low=Decimal(close),
        close=Decimal(close),
        volume=Decimal("1"),
    )


def _instrument(symbol: str, iid: str | None = None, asset_class: str = "equity") -> Instrument:
    return Instrument(
        id=iid or symbol.lower(),
        symbol=symbol,
        name=symbol,
        asset_class=asset_class,
        quantity_step=Decimal("1"),
        min_quantity=Decimal("1"),
    )


def _long_pos(
    *,
    symbol: str = "NVDA",
    iid: str = "nvda",
    sl: str = "95",
    tp: str = "110",
    timeframe: str = "15m",
) -> Position:
    return Position(
        id=f"pos-{symbol.lower()}",
        portfolio_id="p1",
        strategy_instance_id="si1",
        instrument_id=iid,
        direction=Direction.LONG,
        quantity=Decimal("10"),
        entry_price=Decimal("100"),
        stop_loss=Decimal(sl),
        take_profit=Decimal(tp),
        current_price=Decimal("100"),
        status=PositionStatus.OPEN,
        opened_at=datetime(2026, 9, 14, 14, 0, tzinfo=TZ),
        strategy_version_id="sv1",
    )


def _instance(timeframe: str = "15m", iid: str = "nvda") -> StrategyInstance:
    return StrategyInstance(
        id="si1",
        portfolio_id="p1",
        strategy_slug="momentum-continuation",
        strategy_version="1.0.0",
        strategy_version_id="sv1",
        instrument_id=iid,
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
            initial_capital=Decimal("10000"),
            balance=Decimal("10000"),
            equity=Decimal("10000"),
            status=PortfolioStatus.ACTIVE,
        ),
        positions=[pos],
    )


def _pm_patch_stack(stack: ExitStack):
    stack.enter_context(
        patch(
            "quantara_engine.trading.trading_controls.load_trading_control",
            return_value=MagicMock(),
        )
    )
    stack.enter_context(
        patch(
            "quantara_engine.trading.trading_controls.allows_position_management",
            return_value=True,
        )
    )


def test_symbol_classifiers():
    assert is_fast_protection_equity("NVDA")
    assert is_fast_protection_equity("TSLA")
    assert is_fast_protection_equity("AMD")
    assert is_fast_protection_equity("COIN")
    assert is_fast_protection_fx("XAUUSD")
    assert is_fast_protection_fx("GBPJPY")
    assert is_non_crypto_fast_protection("NVDA")
    assert is_fast_1m_protected_symbol("NVDA")
    assert is_fast_1m_protected_symbol("BTCUSD")
    assert not is_non_crypto_fast_protection("BTCUSD")


def test_no_fetch_without_open_positions():
    store = MagicMock()
    store.get_settings_dict.return_value = {"paper_trading_enabled": True}
    after_hours = datetime(2026, 9, 14, 21, 0, tzinfo=TZ)
    with ExitStack() as stack:
        _pm_patch_stack(stack)
        stack.enter_context(
            patch(
                "quantara_engine.execution.non_crypto_fast_protection._collect_research_work",
                return_value=[],
            )
        )
        stack.enter_context(
            patch(
                "quantara_engine.execution.non_crypto_fast_protection._collect_live_sim_rows",
                return_value=[],
            )
        )
        stack.enter_context(
            patch(
                "quantara_engine.execution.non_crypto_fast_protection._fetch_alpaca_1m_batch",
            )
        )
        report = run_non_crypto_fast_protection(store, after_hours)

    assert report["status"] == "skipped"
    assert report["reason"] == "no_mark_or_protection_work"
    assert report["fetches"] == 0


def test_open_nvda_triggers_fast_monitoring():
    store = MagicMock()
    store.get_settings_dict.return_value = {"paper_trading_enabled": True}
    nvda = _instrument("NVDA")
    pos = _long_pos()
    instance = _instance()

    with ExitStack() as stack:
        _pm_patch_stack(stack)
        batch_mock = stack.enter_context(
            patch(
                "quantara_engine.execution.non_crypto_fast_protection._fetch_alpaca_1m_batch",
                return_value=(1, {"nvda": []}),
            )
        )
        stack.enter_context(
            patch(
                "quantara_engine.execution.non_crypto_fast_protection._collect_research_work",
                return_value=[(pos, instance, nvda)],
            )
        )
        stack.enter_context(
            patch(
                "quantara_engine.execution.non_crypto_fast_protection._collect_live_sim_rows",
                return_value=[],
            )
        )
        stack.enter_context(
            patch(
                "quantara_engine.execution.non_crypto_fast_protection._process_research",
                return_value={"checked": 1, "closed": 0},
            )
        )
        stack.enter_context(
            patch(
                "quantara_engine.execution.non_crypto_fast_protection._process_live_sim",
                return_value={"checked": 0, "closed": 0},
            )
        )
        stack.enter_context(
            patch(
                "quantara_engine.execution.non_crypto_fast_protection.apply_fast_1m_marks",
                return_value={"applied_symbols": []},
            )
        )
        store.get_instrument_by_symbol.return_value = nvda
        store.latest_candle_timestamp.return_value = None
        report = run_non_crypto_fast_protection(store, RTH_OPEN)

    assert report["status"] == "success"
    assert report["alpaca_batches"] == 1
    batch_mock.assert_called_once()


def test_alpaca_batched_not_per_symbol():
    store = MagicMock()
    store.get_settings_dict.return_value = {"paper_trading_enabled": True}
    symbols = ["NVDA", "TSLA", "AMD"]
    work = []
    for sym in symbols:
        inst = _instrument(sym, sym.lower())
        work.append((_long_pos(symbol=sym, iid=sym.lower()), _instance(iid=sym.lower()), inst))

    with ExitStack() as stack:
        _pm_patch_stack(stack)
        batch_mock = stack.enter_context(
            patch(
                "quantara_engine.execution.non_crypto_fast_protection._fetch_alpaca_1m_batch",
                return_value=(1, {}),
            )
        )
        stack.enter_context(
            patch(
                "quantara_engine.execution.non_crypto_fast_protection._collect_research_work",
                return_value=work,
            )
        )
        stack.enter_context(
            patch(
                "quantara_engine.execution.non_crypto_fast_protection._collect_live_sim_rows",
                return_value=[],
            )
        )
        stack.enter_context(
            patch(
                "quantara_engine.execution.non_crypto_fast_protection._process_research",
                return_value={"checked": 3, "closed": 0},
            )
        )
        stack.enter_context(
            patch(
                "quantara_engine.execution.non_crypto_fast_protection._process_live_sim",
                return_value={"checked": 0, "closed": 0},
            )
        )
        stack.enter_context(
            patch(
                "quantara_engine.execution.non_crypto_fast_protection.apply_fast_1m_marks",
                return_value={"applied_symbols": []},
            )
        )
        store.get_instrument_by_symbol.side_effect = lambda s: _instrument(s, s.lower())
        store.latest_candle_timestamp.return_value = None
        report = run_non_crypto_fast_protection(store, RTH_OPEN)

    assert report["fetches"] == 1
    assert batch_mock.call_count == 1


def test_closed_equity_session_no_alpaca_polling():
    store = MagicMock()
    store.get_settings_dict.return_value = {"paper_trading_enabled": True}
    nvda = _instrument("NVDA")
    pos = _long_pos()
    instance = _instance()
    after_hours = datetime(2026, 9, 14, 21, 0, tzinfo=TZ)  # 17:00 ET

    with ExitStack() as stack:
        _pm_patch_stack(stack)
        batch_mock = stack.enter_context(
            patch("quantara_engine.execution.non_crypto_fast_protection._fetch_alpaca_1m_batch")
        )
        stack.enter_context(
            patch(
                "quantara_engine.execution.non_crypto_fast_protection._collect_research_work",
                return_value=[(pos, instance, nvda)],
            )
        )
        stack.enter_context(
            patch(
                "quantara_engine.execution.non_crypto_fast_protection._collect_live_sim_rows",
                return_value=[],
            )
        )
        stack.enter_context(
            patch(
                "quantara_engine.execution.non_crypto_fast_protection._process_research",
                return_value={"checked": 1, "closed": 0},
            )
        )
        stack.enter_context(
            patch(
                "quantara_engine.execution.non_crypto_fast_protection._process_live_sim",
                return_value={"checked": 0, "closed": 0},
            )
        )
        stack.enter_context(
            patch(
                "quantara_engine.execution.non_crypto_fast_protection.apply_fast_1m_marks",
                return_value={"applied_symbols": []},
            )
        )
        store.get_instrument_by_symbol.return_value = nvda
        store.latest_candle_timestamp.return_value = None
        report = run_non_crypto_fast_protection(store, after_hours)

    batch_mock.assert_not_called()
    assert report["alpaca_batches"] == 0


@pytest.mark.parametrize("strategy_tf", ["5m", "15m", "1h"])
def test_1m_sl_tp_on_nvda(strategy_tf: str):
    sl_1m = _candle_1m(
        datetime(2026, 9, 14, 14, 33, tzinfo=TZ),
        high="101",
        low="94",
        close="95",
    )
    pos = _long_pos(sl="95")
    state = _portfolio_state(pos)
    instance = _instance(timeframe=strategy_tf)
    nvda = _instrument("NVDA")

    class _Store:
        def __init__(self):
            self.candles = [sl_1m]
            self.cursors: dict[str, str] = {}
            self.decisions = []
            self.last_exit = None

        def get_settings_dict(self):
            return {"position_management_cursors": self.cursors}

        def update_settings(self, key, value, description=None, flush=True):
            if key == "position_management_cursors":
                self.cursors = value

        def list_candles(self, instrument_id, timeframe, since=None, limit=None):
            return [sl_1m]

        def list_recent_candles(self, instrument_id, timeframe, limit=1):
            return [sl_1m]

        def load_portfolio_state(self, portfolio_id):
            return state

        def update_open_position_mark(self, position_id, mark, upnl, flush=True):
            pass

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
            pass

        def trade_exists_for_position(self, position_id):
            return False

    store = _Store()
    now = datetime(2026, 9, 14, 14, 34, tzinfo=TZ)

    with patch("quantara_engine.broker.execution_bridge.execute_through_broker", return_value=None):
        result = process_position_management(
            store,
            position=pos,
            instance=instance,
            instrument=nvda,
            now=now,
            monitor_timeframe=FAST_PROTECTION_TIMEFRAME,
            execution_timeframe=FAST_PROTECTION_TIMEFRAME,
            idempotency_prefix="pm1m",
        )

    assert result["status"] == "closed"
    assert result["exit_reason"] == "sl"


def test_fx_open_position_fetches_when_credits_available():
    store = MagicMock()
    store.get_settings_dict.return_value = {"paper_trading_enabled": True}
    xau = _instrument("XAUUSD", "xau", asset_class="fx")
    pos = _long_pos(symbol="XAUUSD", iid="xau")
    instance = _instance(iid="xau")

    with ExitStack() as stack:
        _pm_patch_stack(stack)
        fx_fetch = stack.enter_context(
            patch(
                "quantara_engine.execution.non_crypto_fast_protection._fetch_twelve_data_1m",
                return_value=[
                    _candle_1m(datetime(2026, 9, 14, 12, 1, tzinfo=TZ), high="2000", iid="xau")
                ],
            )
        )
        stack.enter_context(
            patch(
                "quantara_engine.execution.non_crypto_fast_protection._collect_research_work",
                return_value=[(pos, instance, xau)],
            )
        )
        stack.enter_context(
            patch(
                "quantara_engine.execution.non_crypto_fast_protection._collect_live_sim_rows",
                return_value=[],
            )
        )
        stack.enter_context(
            patch(
                "quantara_engine.execution.non_crypto_fast_protection.can_run_fast_fx_fetch",
                return_value=True,
            )
        )
        stack.enter_context(
            patch(
                "quantara_engine.execution.non_crypto_fast_protection._process_research",
                return_value={"checked": 1, "closed": 0},
            )
        )
        stack.enter_context(
            patch(
                "quantara_engine.execution.non_crypto_fast_protection._process_live_sim",
                return_value={"checked": 0, "closed": 0},
            )
        )
        stack.enter_context(
            patch(
                "quantara_engine.execution.non_crypto_fast_protection.apply_fast_1m_marks",
                return_value={"applied_symbols": ["XAUUSD"]},
            )
        )
        stack.enter_context(
            patch(
                "quantara_engine.execution.non_crypto_fast_protection.fx_fast_budget_report",
                return_value={"remaining_fast_budget": 400},
            )
        )
        store.get_instrument_by_symbol.return_value = xau
        store.latest_candle_timestamp.return_value = None
        store.list_candles.return_value = []
        report = run_non_crypto_fast_protection(store, datetime(2026, 9, 14, 12, 5, tzinfo=TZ))

    fx_fetch.assert_called_once()
    assert "XAUUSD" in report["fx_fetched"]


def test_fx_credit_guard_fallback_to_5m():
    store = MagicMock()
    store.get_settings_dict.return_value = {"paper_trading_enabled": True}
    xau = _instrument("XAUUSD", "xau", asset_class="fx")
    pos = _long_pos(symbol="XAUUSD", iid="xau")
    instance = _instance(iid="xau")

    with ExitStack() as stack:
        _pm_patch_stack(stack)
        fx_fetch = stack.enter_context(
            patch("quantara_engine.execution.non_crypto_fast_protection._fetch_twelve_data_1m")
        )
        stack.enter_context(
            patch(
                "quantara_engine.execution.non_crypto_fast_protection._collect_research_work",
                return_value=[(pos, instance, xau)],
            )
        )
        stack.enter_context(
            patch(
                "quantara_engine.execution.non_crypto_fast_protection._collect_live_sim_rows",
                return_value=[],
            )
        )
        stack.enter_context(
            patch(
                "quantara_engine.execution.non_crypto_fast_protection.can_run_fast_fx_fetch",
                return_value=False,
            )
        )
        stack.enter_context(
            patch(
                "quantara_engine.execution.non_crypto_fast_protection._process_research",
                return_value={"checked": 1, "closed": 0},
            )
        )
        stack.enter_context(
            patch(
                "quantara_engine.execution.non_crypto_fast_protection._process_live_sim",
                return_value={"checked": 0, "closed": 0},
            )
        )
        stack.enter_context(
            patch(
                "quantara_engine.execution.non_crypto_fast_protection.apply_fast_1m_marks",
                return_value={"applied_symbols": []},
            )
        )
        stack.enter_context(
            patch(
                "quantara_engine.execution.non_crypto_fast_protection.fx_fast_budget_report",
                return_value={"remaining_fast_budget": 0},
            )
        )
        store.get_instrument_by_symbol.return_value = xau
        store.latest_candle_timestamp.return_value = None
        report = run_non_crypto_fast_protection(store, datetime(2026, 9, 14, 12, 5, tzinfo=TZ))

    fx_fetch.assert_not_called()
    assert "XAUUSD" in report["fx_skipped_credit"]


def test_both_fx_open_alternates_budget():
    now = datetime(2026, 9, 14, 12, 7, tzinfo=TZ)
    selected = select_fx_symbols_this_cycle(["XAUUSD", "GBPJPY"], now=now)
    assert len(selected) == 1
    assert selected[0] in ("XAUUSD", "GBPJPY")
    other_minute = now.replace(minute=8)
    selected2 = select_fx_symbols_this_cycle(["XAUUSD", "GBPJPY"], now=other_minute)
    assert len(selected2) == 1


def test_credit_guard_never_exceeds_internal_limit():
    store = MagicMock()
    used = INTERNAL_GUARD_LIMIT - SCHEDULED_INGEST_RESERVE
    with patch(
        "quantara_engine.execution.fx_fast_credit_guard.status_payload",
        return_value={"used_today": used},
    ), patch(
        "quantara_engine.execution.fx_fast_credit_guard.can_fetch",
        return_value=False,
    ):
        assert remaining_fast_fx_budget(store) == 0
        assert can_run_fast_fx_fetch(store) is False


def test_1m_mark_not_overwritten_by_older_5m():
    store = MagicMock()
    store.get_settings_dict.return_value = {
        FAST_CANONICAL_MARKS_KEY: {
            "NVDA": {
                "price": "105.50",
                "at": datetime(2026, 9, 14, 14, 37, tzinfo=TZ).isoformat(),
            }
        }
    }
    older_5m = datetime(2026, 9, 14, 14, 35, tzinfo=TZ)
    apply_fast_1m_marks(store, {"NVDA": (Decimal("104.00"), older_5m)}, flush=False)
    stored = store.update_settings.call_args_list[-1][0][1]
    assert stored["NVDA"]["price"] == "105.50"


def test_fx_credit_guard_falls_back_to_5m_pm():
    store = MagicMock()
    with patch(
        "quantara_engine.execution.fx_fast_credit_guard.can_run_fast_fx_fetch",
        return_value=False,
    ):
        from quantara_engine.execution.crypto_mark_valuation import (
            should_skip_5m_position_management,
        )

        assert should_skip_5m_position_management(
            store,
            "XAUUSD",
            now=datetime(2026, 9, 14, 12, 5, tzinfo=TZ),
        ) is False


def test_manage_all_open_positions_skips_fast_symbols():
    store = MagicMock()
    nvda = _instrument("NVDA")
    pos = _long_pos()
    instance = _instance()
    store.get_settings_dict.return_value = {"paper_trading_enabled": True}
    store.list_all_competition_entries.return_value = (
        [],
        [],
        [{"instance": instance, "portfolio": MagicMock(id="p1")}],
    )
    store.batch_load_portfolio_states.return_value = {"p1": _portfolio_state(pos)}
    store.batch_open_positions_by_portfolio.return_value = {"p1": [pos]}
    store.get_instrument_by_id.return_value = nvda

    with ExitStack() as stack:
        _pm_patch_stack(stack)
        stack.enter_context(
            patch(
                "quantara_engine.trading.trading_controls.requires_flatten",
                return_value=False,
            )
        )
        stack.enter_context(
            patch(
                "quantara_engine.portfolio.currency.build_currency_context",
                return_value=MagicMock(),
            )
        )
        stack.enter_context(
            patch(
                "quantara_engine.execution.position_management._get_cursors",
                return_value={},
            )
        )
        pm_mock = stack.enter_context(
            patch("quantara_engine.execution.position_management.process_position_management")
        )
        report = manage_all_open_positions(store, RTH_OPEN)

    pm_mock.assert_not_called()
    assert report["positions_attempted"] == 0


def test_live_sim_exits_skips_fast_symbols():
    store = MagicMock()
    store.session.execute.return_value.mappings.return_value.first.return_value = {"id": "acct-1"}
    store.session.execute.return_value.mappings.return_value.all.return_value = [
        {
            "id": "ls-1",
            "instrument_id": "nvda",
            "direction": "long",
            "quantity": Decimal("10"),
            "entry_price": Decimal("100"),
            "stop_loss": Decimal("95"),
            "take_profit": Decimal("110"),
            "timeframe": "15m",
            "canonical_opportunity_key": "k",
            "symbol": "NVDA",
        }
    ]

    with patch("quantara_engine.live_sim.position_management.execute_through_broker") as broker_mock:
        result = process_live_sim_exits(store, RTH_OPEN)

    broker_mock.assert_not_called()
    assert result["closed"] == 0


def test_alpaca_fetch_equity_1m_batch_parses_multi_symbol():
    from quantara_engine.market_data.adapters.alpaca import AlpacaMarketDataProvider

    provider = AlpacaMarketDataProvider(store=None, caller="test")
    since = datetime(2026, 9, 14, 14, 0, tzinfo=TZ)
    rows = {
        "NVDA": [{"t": "2026-09-14T14:01:00Z", "o": "100", "h": "102", "l": "99", "c": "101", "v": 1000}],
        "TSLA": [{"t": "2026-09-14T14:01:00Z", "o": "200", "h": "202", "l": "199", "c": "201", "v": 1000}],
    }

    with patch.object(provider, "_fetch_equity_bars_batch", return_value=rows), patch(
        "quantara_engine.market_data.adapters.alpaca.is_bar_complete",
        return_value=True,
    ):
        result = provider.fetch_equity_1m_batch(
            [("nvda-id", "NVDA"), ("tsla-id", "TSLA")],
            since=since,
        )

    assert "nvda-id" in result
    assert "tsla-id" in result
    assert result["nvda-id"][0].close == Decimal("101")
