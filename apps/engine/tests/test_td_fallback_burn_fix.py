"""Twelve Data emergency-fallback burn fix — focused regression tests A–Q."""

from __future__ import annotations

from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from decimal import Decimal
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
from quantara_engine.execution.fx_protection_sources import (
    TD_FAR_MIN_INTERVAL,
    last_provider_attempt_at,
    mark_provider_attempted,
    mark_provider_success,
    plan_fx_protection_fetch,
)
from quantara_engine.execution.non_crypto_fast_protection import run_non_crypto_fast_protection
from quantara_engine.market_data.adapters.twelvedata import TwelveDataMarketDataProvider
from quantara_engine.market_data.credits import ENDPOINT_CREDITS, FetchPriority, safe_used_today
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
        source="tiingo",
        is_complete=True,
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


def _run_fx_cycle(
    *,
    store: MagicMock,
    research,
    now: datetime,
    tiingo_return,
    td_return=None,
    can_td: bool = True,
    tiingo_cooldown: bool = False,
):
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
                return_value=tiingo_cooldown,
            )
        )
        stack.enter_context(
            patch(
                "quantara_engine.execution.non_crypto_fast_protection.provider_can_request",
                return_value=not tiingo_cooldown,
            )
        )
        stack.enter_context(
            patch(
                "quantara_engine.execution.non_crypto_fast_protection.can_fetch_twelve_data_1m",
                return_value=can_td,
            )
        )
        tiingo = stack.enter_context(
            patch(
                "quantara_engine.execution.non_crypto_fast_protection._fetch_tiingo_1m",
                return_value=tiingo_return,
            )
        )
        td = stack.enter_context(
            patch(
                "quantara_engine.execution.non_crypto_fast_protection._fetch_twelve_data_1m",
                return_value=td_return or [],
            )
        )
        stack.enter_context(
            patch(
                "quantara_engine.execution.non_crypto_fast_protection._process_research",
                return_value={"checked": len(research), "closed": 0},
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
        report = run_non_crypto_fast_protection(store, now)
    return report, tiingo, td


# --- A/B: fresh stored + Tiingo empty/429 → TD = 0 ---


def test_a_fresh_stored_tiingo_empty_no_td():
    store = MagicMock()
    settings: dict = {"paper_trading_enabled": True}
    store.get_settings_dict.return_value = settings
    store.update_settings.side_effect = lambda k, v, **kw: settings.__setitem__(k, v)

    xau = _instrument("XAUUSD", "xau")
    pos = _pos(pid="1", iid="xau", entry="2000", sl="1990")  # far from SL at 2000
    now = datetime(2026, 9, 14, 12, 5, tzinfo=TZ)
    fresh = _candle(now - timedelta(minutes=1), high="2001", low="1999", close="2000")
    store.get_instrument_by_symbol.return_value = xau
    store.list_candles.return_value = [fresh]
    store.latest_candle_timestamp.return_value = fresh.timestamp

    report, _ti, td = _run_fx_cycle(
        store=store,
        research=[(pos, _instance(), xau)],
        now=now,
        tiingo_return=[],
    )
    assert td.call_count == 0
    assert report["fx_td_calls"] == 0
    assert report["fx_sources"]["XAUUSD"] == "stored_1m"


def test_b_fresh_stored_tiingo_429_no_td():
    store = MagicMock()
    settings: dict = {"paper_trading_enabled": True}
    store.get_settings_dict.return_value = settings
    store.update_settings.side_effect = lambda k, v, **kw: settings.__setitem__(k, v)

    xau = _instrument("XAUUSD", "xau")
    pos = _pos(pid="1", iid="xau")
    now = datetime(2026, 9, 14, 12, 5, tzinfo=TZ)
    fresh = _candle(now - timedelta(minutes=1), high="2001", low="1999", close="2000")
    store.get_instrument_by_symbol.return_value = xau
    store.list_candles.return_value = [fresh]
    store.latest_candle_timestamp.return_value = fresh.timestamp

    report, _ti, td = _run_fx_cycle(
        store=store,
        research=[(pos, _instance(), xau)],
        now=now,
        tiingo_return=[],
        tiingo_cooldown=True,
    )
    assert td.call_count == 0
    assert report["fx_td_calls"] == 0


# --- C/D/E: attempt timestamps throttle ---


def test_c_d_e_failed_attempts_throttle():
    store = MagicMock()
    settings: dict = {}
    store.get_settings_dict.return_value = settings
    store.update_settings.side_effect = lambda k, v, **kw: settings.__setitem__(k, v)

    now = datetime(2026, 9, 14, 12, 0, tzinfo=TZ)
    mark_provider_attempted(store, "XAUUSD", "tiingo", now=now)
    mark_provider_attempted(store, "XAUUSD", "twelvedata", now=now)

    assert last_provider_attempt_at(store, "XAUUSD", "tiingo") == now
    assert last_provider_attempt_at(store, "XAUUSD", "twelvedata") == now

    # One minute later — TD interval not elapsed (far = 5m)
    later = now + timedelta(minutes=1)
    plan = plan_fx_protection_fetch(
        store,
        "XAUUSD",
        quota_mode="FALLBACK",
        tiingo_eligible=False,
        td_eligible=True,
        near_sl=False,
        has_fresh_stored_1m=False,
        now=later,
    )
    assert plan.fetch_provider is None
    assert plan.reason == "td_throttled"

    # After FAR interval — TD allowed
    after = now + TD_FAR_MIN_INTERVAL
    plan2 = plan_fx_protection_fetch(
        store,
        "XAUUSD",
        quota_mode="FALLBACK",
        tiingo_eligible=False,
        td_eligible=True,
        near_sl=False,
        has_fresh_stored_1m=False,
        now=after,
    )
    assert plan2.fetch_provider == "twelvedata"


# --- F: invalid range no HTTP ---


def test_f_invalid_range_skips_http():
    provider = TwelveDataMarketDataProvider.__new__(TwelveDataMarketDataProvider)
    provider.api_key = "test-key"
    provider._store = None
    provider._caller = "test"
    provider._priority = FetchPriority.OPEN_POSITION
    provider._allow_non_canonical_timeframes = True
    provider._asset = None
    provider._http_timeout = 5
    provider.provider_symbol = "XAU/USD"

    called = {"n": 0}

    def _boom(*_a, **_k):
        called["n"] += 1
        raise AssertionError("HTTP must not be called")

    provider._request = _boom  # type: ignore[method-assign]
    start = datetime(2026, 9, 14, 12, 5, tzinfo=TZ)
    end = datetime(2026, 9, 14, 12, 4, tzinfo=TZ)
    assert provider.fetch_range("xau", "5m", start, end) == []
    assert called["n"] == 0
    # fetch_latest with since >= now → empty without HTTP
    future = datetime.now(TZ) + timedelta(hours=1)
    assert provider.fetch_latest("xau", "1m", since=future) == []
    assert called["n"] == 0
    # start==end inside _time_series also skips
    assert provider._time_series("5m", start_date=start, end_date=start) == []
    assert called["n"] == 0


# --- G: fresh stored continues SL/TP ---


def test_g_fresh_stored_sl_tp_protection():
    pos = _pos(pid="1", iid="xau", entry="2000", sl="1990", tp="2010")
    sl_bar = _candle(datetime(2026, 9, 14, 12, 1, tzinfo=TZ), high="2001", low="1989", close="1995")
    assert detect_exit_trigger(pos, sl_bar)[0].value == "sl"


# --- H/I: near-SL + stale → TD emergency with cadence ---


def test_h_i_near_sl_stale_td_emergency_and_interval():
    store = MagicMock()
    settings: dict = {}
    store.get_settings_dict.return_value = settings
    store.update_settings.side_effect = lambda k, v, **kw: settings.__setitem__(k, v)

    now = datetime(2026, 9, 14, 12, 0, tzinfo=TZ)
    plan = plan_fx_protection_fetch(
        store,
        "XAUUSD",
        quota_mode="NORMAL",
        tiingo_eligible=False,
        td_eligible=True,
        near_sl=True,
        has_fresh_stored_1m=False,
        now=now,
    )
    assert plan.fetch_provider == "twelvedata"
    assert plan.reason == "td_emergency_stale"

    mark_provider_attempted(store, "XAUUSD", "twelvedata", now=now)
    plan2 = plan_fx_protection_fetch(
        store,
        "XAUUSD",
        quota_mode="NORMAL",
        tiingo_eligible=False,
        td_eligible=True,
        near_sl=True,
        has_fresh_stored_1m=False,
        now=now + timedelta(minutes=1),
    )
    assert plan2.fetch_provider is None  # near interval = 2m


# --- J: TD blocked → 5m fail-safe ---


def test_j_td_blocked_uses_5m_failsafe():
    store = MagicMock()
    store.get_settings_dict.return_value = {}
    plan = plan_fx_protection_fetch(
        store,
        "XAUUSD",
        quota_mode="EXHAUSTED",
        tiingo_eligible=False,
        td_eligible=False,
        near_sl=True,
        has_fresh_stored_1m=False,
        now=datetime(2026, 9, 14, 12, tzinfo=TZ),
    )
    assert plan.fetch_provider is None
    assert plan.source == "5m_fallback"


# --- K: quota reset does not create heartbeat ---


def test_k_provider_day_reset_no_td_heartbeat():
    store = MagicMock()
    settings: dict = {}
    store.get_settings_dict.return_value = settings
    store.update_settings.side_effect = lambda k, v, **kw: settings.__setitem__(k, v)

    now = datetime(2026, 9, 15, 0, 5, tzinfo=TZ)  # after UTC day reset
    # Fresh stored after reset — TD must stay zero even with open positions / TD eligible
    for minute in range(60):
        t = now + timedelta(minutes=minute)
        plan = plan_fx_protection_fetch(
            store,
            "XAUUSD",
            quota_mode="NORMAL",
            tiingo_eligible=False,  # simulate Tiingo empty/429
            td_eligible=True,
            near_sl=False,
            has_fresh_stored_1m=True,
            now=t,
        )
        assert plan.fetch_provider != "twelvedata", f"minute={minute}"
        assert plan.source == "stored_1m"


# --- L/M: many positions one plan ---


def test_l_m_many_positions_one_symbol_plan():
    store = MagicMock()
    settings: dict = {"paper_trading_enabled": True}
    store.get_settings_dict.return_value = settings
    store.update_settings.side_effect = lambda k, v, **kw: settings.__setitem__(k, v)

    xau = _instrument("XAUUSD", "xau")
    gbp = _instrument("GBPJPY", "gbp")
    now = datetime(2026, 9, 14, 12, 5, tzinfo=TZ)
    fresh_x = _candle(now - timedelta(minutes=1), high="2001", low="1999", close="2000", iid="xau")
    fresh_g = _candle(now - timedelta(minutes=1), high="191", low="189", close="190", iid="gbp")

    research = [(_pos(pid=f"x{i}", iid="xau"), _instance(), xau) for i in range(5)] + [
        (_pos(pid=f"g{i}", iid="gbp", entry="190", sl="189", tp="192"), _instance(), gbp)
        for i in range(11)
    ]

    def _candles(iid, *a, **k):
        return [fresh_x] if iid == "xau" else [fresh_g]

    store.get_instrument_by_symbol.side_effect = lambda s: xau if s == "XAUUSD" else gbp
    store.list_candles.side_effect = _candles
    store.latest_candle_timestamp.side_effect = lambda iid, *a, **k: (
        fresh_x.timestamp if iid == "xau" else fresh_g.timestamp
    )

    report, tiingo, td = _run_fx_cycle(
        store=store,
        research=research,
        now=now,
        tiingo_return=[],
    )
    # Fresh stored → scheduled Tiingo may run once per symbol if interval elapsed (no prior attempt)
    # After empty Tiingo, no TD. Max 2 Tiingo calls (one per symbol), 0 TD.
    assert tiingo.call_count <= 2
    assert td.call_count == 0
    assert report["fx_td_calls"] == 0


# --- N: api_usage cost model ---


def test_n_api_usage_costs_one_credit():
    assert ENDPOINT_CREDITS["api_usage"] == 1


# --- O: provider floor ---


def test_o_provider_floor_safe():
    store = MagicMock()
    today = datetime.now(timezone.utc).date().isoformat()
    store.get_settings_dict.return_value = {
        "provider_credits:twelvedata": {"date": today, "used": 10, "events": []},
        "provider_health:twelvedata": {"date": today, "provider_daily_usage": 50},
    }
    assert safe_used_today(store) == 50


# --- P/Q: ledger event fields without secrets ---


def test_p_q_record_usage_attribution_no_secrets():
    from quantara_engine.market_data.credits import CreditEvent, record_usage

    store = MagicMock()
    today = datetime.now(timezone.utc).date().isoformat()
    state = {"date": today, "used": 0, "events": []}
    store.get_settings_dict.return_value = {"provider_credits:twelvedata": state}
    store.session = MagicMock()

    with patch("quantara_engine.market_data.credits._record_usage_on_session") as rec:
        rec.return_value = 1
        ev = record_usage(
            store,
            endpoint="time_series",
            symbol="XAU/USD",
            interval="1min",
            caller="non_crypto_fast_protection",
            success=False,
            reason="HTTP 400: bad; apikey=SECRET123 token=abc",
            priority="OPEN_POSITION",
            http_status=400,
            charge=True,
        )
    assert isinstance(ev, CreditEvent)
    assert ev.success is False
    assert ev.priority == "OPEN_POSITION"
    assert "SECRET" not in (ev.reason or "")
    assert "token=" not in (ev.reason or "").lower()
    assert ev.reason == "redacted"


def test_success_marks_both_timestamps():
    store = MagicMock()
    settings: dict = {}
    store.get_settings_dict.return_value = settings
    store.update_settings.side_effect = lambda k, v, **kw: settings.__setitem__(k, v)
    now = datetime(2026, 9, 14, 12, tzinfo=TZ)
    mark_provider_success(store, "GBPJPY", "tiingo", now=now)
    entry = settings["fx_protection:provider_fetch_state"]["GBPJPY:tiingo"]
    assert entry["last_attempt_at"] == now.isoformat()
    assert entry["last_success_at"] == now.isoformat()
