"""Quota-safe FX protection: per-symbol fetches, Tiingo primary, TD fallback, credits."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from contextlib import ExitStack
from unittest.mock import MagicMock, patch

from quantara_engine.domain.types import (
    Candle,
    Direction,
    Instrument,
    Position,
    PositionStatus,
    StrategyInstance,
)
from quantara_engine.execution.exit_triggers import detect_exit_trigger
from quantara_engine.execution.fx_fast_credit_guard import quota_mode
from quantara_engine.execution.fx_protection_sources import (
    is_near_stop,
    plan_fx_protection_fetch,
)
from quantara_engine.execution.non_crypto_fast_protection import run_non_crypto_fast_protection
from quantara_engine.market_data.credits import safe_used_today
from quantara_engine.market_data.polling import FAST_PROTECTION_TIMEFRAME

TZ = timezone.utc


def _instrument(symbol: str, iid: str) -> Instrument:
    return Instrument(
        id=iid,
        symbol=symbol,
        name=symbol,
        asset_class="fx",
        quote_currency="USD",
        base_currency="XAU" if symbol.startswith("XAU") else "GBP",
        quantity_step=Decimal("1"),
        min_quantity=Decimal("1"),
    )


def _pos(
    *,
    pid: str,
    iid: str,
    entry: str = "2000",
    sl: str = "1990",
    tp: str = "2020",
    direction: Direction = Direction.LONG,
) -> Position:
    return Position(
        id=pid,
        portfolio_id="p1",
        strategy_instance_id="s1",
        instrument_id=iid,
        direction=direction,
        quantity=Decimal("1"),
        entry_price=Decimal(entry),
        stop_loss=Decimal(sl),
        take_profit=Decimal(tp),
        current_price=Decimal(entry),
        opened_at=datetime(2026, 9, 14, 10, tzinfo=TZ),
        status=PositionStatus.OPEN,
    )


def _instance() -> StrategyInstance:
    return StrategyInstance(
        id="s1",
        portfolio_id="p1",
        strategy_slug="fx-test",
        strategy_version="1.0.0",
        strategy_version_id="sv1",
        instrument_id="xau",
        timeframe="5m",
        risk_profile_id="rp1",
        parameter_overrides={},
    )


def _pm_patch(stack: ExitStack) -> None:
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


def _candle(ts: datetime, *, high: str, low: str, close: str, iid: str = "xau") -> Candle:
    return Candle(
        instrument_id=iid,
        timeframe=FAST_PROTECTION_TIMEFRAME,
        timestamp=ts,
        open=Decimal(close),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
        volume=None,
        source="test",
        is_complete=True,
    )


def test_near_sl_rule_uses_half_r():
    pos = _pos(pid="1", iid="xau", entry="2000", sl="1990")
    assert is_near_stop(pos, Decimal("1994")) is True
    assert is_near_stop(pos, Decimal("1996")) is False


def test_plan_prefers_stored_or_tiingo_over_td():
    store = MagicMock()
    store.get_settings_dict.return_value = {}
    now = datetime(2026, 9, 14, 12, 5, tzinfo=TZ)
    plan = plan_fx_protection_fetch(
        store,
        "XAUUSD",
        quota_mode="NORMAL",
        tiingo_eligible=True,
        td_eligible=True,
        near_sl=False,
        has_fresh_stored_1m=True,
        now=now,
    )
    assert plan.fetch_provider in (None, "tiingo")
    assert plan.fetch_provider != "twelvedata"

    plan2 = plan_fx_protection_fetch(
        store,
        "XAUUSD",
        quota_mode="NORMAL",
        tiingo_eligible=True,
        td_eligible=True,
        near_sl=False,
        has_fresh_stored_1m=False,
        now=now,
    )
    assert plan2.fetch_provider == "tiingo"


def test_many_positions_one_fetch_per_symbol():
    """A/B/C: 38 Research FX legs must not multiply provider calls."""
    store = MagicMock()
    store.get_settings_dict.return_value = {"paper_trading_enabled": True}
    xau = _instrument("XAUUSD", "xau")
    gbp = _instrument("GBPJPY", "gbp")
    instance = _instance()
    xau_positions = [_pos(pid=f"x{i}", iid="xau") for i in range(27)]
    gbp_positions = [
        _pos(pid=f"g{i}", iid="gbp", entry="190", sl="189", tp="192") for i in range(11)
    ]
    research = [(p, instance, xau) for p in xau_positions] + [
        (p, instance, gbp) for p in gbp_positions
    ]

    with ExitStack() as stack:
        _pm_patch(stack)
        stack.enter_context(
            patch(
                "quantara_engine.execution.non_crypto_fast_protection._collect_research_work",
                return_value=research,
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
                "quantara_engine.execution.non_crypto_fast_protection.is_in_cooldown",
                return_value=False,
            )
        )
        stack.enter_context(
            patch(
                "quantara_engine.execution.non_crypto_fast_protection.provider_can_request",
                return_value=True,
            )
        )
        tiingo = stack.enter_context(
            patch(
                "quantara_engine.execution.non_crypto_fast_protection._fetch_tiingo_1m",
                return_value=[
                    _candle(
                        datetime(2026, 9, 14, 12, 4, tzinfo=TZ),
                        high="2001",
                        low="1999",
                        close="2000",
                    )
                ],
            )
        )
        td = stack.enter_context(
            patch(
                "quantara_engine.execution.non_crypto_fast_protection._fetch_twelve_data_1m",
                return_value=[],
            )
        )
        stack.enter_context(
            patch(
                "quantara_engine.execution.non_crypto_fast_protection._process_research",
                return_value={"checked": 38, "closed": 0},
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
                return_value={"applied_symbols": ["XAUUSD", "GBPJPY"]},
            )
        )
        stack.enter_context(
            patch(
                "quantara_engine.execution.non_crypto_fast_protection.fx_fast_budget_report",
                return_value={"remaining_fast_budget": 400, "quota_mode": "NORMAL"},
            )
        )

        def _get_inst(sym):
            return xau if sym == "XAUUSD" else gbp if sym == "GBPJPY" else None

        store.get_instrument_by_symbol.side_effect = _get_inst
        store.list_candles.return_value = []
        store.latest_candle_timestamp.return_value = None
        report = run_non_crypto_fast_protection(store, datetime(2026, 9, 14, 12, 5, tzinfo=TZ))

    assert tiingo.call_count == 2
    assert td.call_count == 0
    assert report["fx_tiingo_calls"] == 2
    assert report["fx_td_calls"] == 0


def test_td_not_called_when_tiingo_succeeds():
    store = MagicMock()
    store.get_settings_dict.return_value = {"paper_trading_enabled": True}
    xau = _instrument("XAUUSD", "xau")
    pos = _pos(pid="1", iid="xau")
    instance = _instance()
    with ExitStack() as stack:
        _pm_patch(stack)
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
                "quantara_engine.execution.non_crypto_fast_protection.is_in_cooldown",
                return_value=False,
            )
        )
        stack.enter_context(
            patch(
                "quantara_engine.execution.non_crypto_fast_protection.provider_can_request",
                return_value=True,
            )
        )
        stack.enter_context(
            patch(
                "quantara_engine.execution.non_crypto_fast_protection._fetch_tiingo_1m",
                return_value=[
                    _candle(
                        datetime(2026, 9, 14, 12, 4, tzinfo=TZ),
                        high="2001",
                        low="1999",
                        close="2000",
                    )
                ],
            )
        )
        td = stack.enter_context(
            patch(
                "quantara_engine.execution.non_crypto_fast_protection._fetch_twelve_data_1m",
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
                return_value={"applied_symbols": ["XAUUSD"]},
            )
        )
        stack.enter_context(
            patch(
                "quantara_engine.execution.non_crypto_fast_protection.fx_fast_budget_report",
                return_value={"remaining_fast_budget": 400, "quota_mode": "NORMAL"},
            )
        )
        store.get_instrument_by_symbol.return_value = xau
        store.list_candles.return_value = []
        store.latest_candle_timestamp.return_value = None
        run_non_crypto_fast_protection(store, datetime(2026, 9, 14, 12, 5, tzinfo=TZ))
    td.assert_not_called()


def test_td_fallback_when_tiingo_fails():
    store = MagicMock()
    store.get_settings_dict.return_value = {"paper_trading_enabled": True}
    xau = _instrument("XAUUSD", "xau")
    pos = _pos(pid="1", iid="xau")
    instance = _instance()
    with ExitStack() as stack:
        _pm_patch(stack)
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
                "quantara_engine.execution.non_crypto_fast_protection.is_in_cooldown",
                return_value=False,
            )
        )
        stack.enter_context(
            patch(
                "quantara_engine.execution.non_crypto_fast_protection.provider_can_request",
                return_value=True,
            )
        )
        stack.enter_context(
            patch(
                "quantara_engine.execution.non_crypto_fast_protection.can_fetch_twelve_data_1m",
                return_value=True,
            )
        )
        stack.enter_context(
            patch(
                "quantara_engine.execution.non_crypto_fast_protection._fetch_tiingo_1m",
                return_value=[],
            )
        )
        td = stack.enter_context(
            patch(
                "quantara_engine.execution.non_crypto_fast_protection._fetch_twelve_data_1m",
                return_value=[
                    _candle(
                        datetime(2026, 9, 14, 12, 4, tzinfo=TZ),
                        high="2001",
                        low="1999",
                        close="2000",
                    )
                ],
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
                return_value={"remaining_fast_budget": 400, "quota_mode": "FALLBACK"},
            )
        )
        stack.enter_context(
            patch(
                "quantara_engine.execution.non_crypto_fast_protection.quota_mode",
                return_value="FALLBACK",
            )
        )
        store.get_instrument_by_symbol.return_value = xau
        store.list_candles.return_value = []
        store.latest_candle_timestamp.return_value = None
        report = run_non_crypto_fast_protection(store, datetime(2026, 9, 14, 12, 5, tzinfo=TZ))
    td.assert_called_once()
    assert report["fx_td_calls"] == 1


def test_sl_tp_and_sl_wins_on_local_1m():
    pos = _pos(pid="1", iid="xau", entry="2000", sl="1990", tp="2010")
    sl_bar = _candle(
        datetime(2026, 9, 14, 12, 1, tzinfo=TZ), high="2001", low="1989", close="1995"
    )
    assert detect_exit_trigger(pos, sl_bar)[0].value == "sl"

    tp_bar = _candle(
        datetime(2026, 9, 14, 12, 2, tzinfo=TZ), high="2011", low="1995", close="2008"
    )
    assert detect_exit_trigger(pos, tp_bar)[0].value == "tp"

    both = _candle(
        datetime(2026, 9, 14, 12, 3, tzinfo=TZ), high="2011", low="1989", close="2000"
    )
    assert detect_exit_trigger(pos, both)[0].value == "sl"


def test_safe_used_prefers_provider_when_higher():
    store = MagicMock()
    store.get_settings_dict.return_value = {
        "provider_credits:twelvedata": {
            "date": datetime.now(timezone.utc).date().isoformat(),
            "used": 4,
            "events": [],
        },
        "provider_health:twelvedata": {
            "date": datetime.now(timezone.utc).date().isoformat(),
            "provider_daily_usage": 303,
        },
    }
    assert safe_used_today(store) == 303


def test_quota_mode_conservation_and_exhausted():
    store = MagicMock()
    with patch("quantara_engine.execution.fx_fast_credit_guard.safe_used_today", return_value=530):
        assert quota_mode(store) == "CONSERVATION"
    with patch("quantara_engine.execution.fx_fast_credit_guard.safe_used_today", return_value=720):
        assert quota_mode(store) == "EXHAUSTED"


def test_1m_minute_boundaries_via_bar_complete():
    from quantara_engine.market_data.polling import is_bar_complete

    ts = datetime(2026, 9, 14, 12, 4, 0, tzinfo=TZ)
    now = datetime(2026, 9, 14, 12, 4, 30, tzinfo=TZ)
    assert is_bar_complete(ts, "1m", now) is False
    assert is_bar_complete(ts, "1m", ts + timedelta(minutes=1)) is True


def test_conservation_far_from_sl_avoids_td():
    store = MagicMock()
    store.get_settings_dict.return_value = {}
    now = datetime(2026, 9, 14, 12, 5, tzinfo=TZ)
    plan = plan_fx_protection_fetch(
        store,
        "XAUUSD",
        quota_mode="CONSERVATION",
        tiingo_eligible=False,
        td_eligible=True,
        near_sl=False,
        has_fresh_stored_1m=False,
        now=now,
    )
    assert plan.fetch_provider is None
    assert plan.source == "5m_fallback"
