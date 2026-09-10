"""Tests for incremental candle derivation and fetch polling guards."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock

from quantara_engine.domain.types import Candle
from quantara_engine.market_data.aggregation import (
    incremental_derive_from_5m,
    incremental_source_limit,
)
from quantara_engine.market_data.credits import DAILY_HARD_LIMIT, _today_key
from quantara_engine.market_data.registry import list_target_assets
from quantara_workers.jobs.fetch_data import _should_poll_asset


def _bar(ts: str, o: str = "1", h: str = "2", l: str = "0.5", c: str = "1.5") -> Candle:
    return Candle(
        instrument_id="inst-1",
        timeframe="5m",
        timestamp=datetime.fromisoformat(ts).replace(tzinfo=timezone.utc),
        open=Decimal(o),
        high=Decimal(h),
        low=Decimal(l),
        close=Decimal(c),
        source="test",
        is_complete=True,
    )


def test_incremental_source_limit_is_small():
    assert incremental_source_limit(1) < 100
    assert incremental_source_limit(5) < 150


def test_incremental_derive_only_touched_15m_bucket():
    bars = [
        _bar("2026-09-08T10:15:00+00:00"),
        _bar("2026-09-08T10:20:00+00:00"),
        _bar("2026-09-08T10:25:00+00:00"),
    ]
    new_ts = [bars[-1].timestamp]
    derived = incremental_derive_from_5m(bars, new_ts, session_mode="utc")
    assert len(derived) == 1
    assert derived[0].timeframe == "15m"


def test_twelve_data_blocked_skips_live_poll():
    class FakeStore:
        def __init__(self, settings: dict):
            self._settings = settings

        def get_settings_dict(self) -> dict:
            return self._settings

    store = FakeStore(
        {
            "provider_credits:twelvedata": {
                "date": _today_key(),
                "used": DAILY_HARD_LIMIT,
                "events": [],
            }
        }
    )
    xau = next(a for a in list_target_assets() if a.db_symbol == "XAUUSD")
    should, reason = _should_poll_asset(
        store,
        xau,
        now=datetime.now(timezone.utc),
        stored=500,
        force_bootstrap=False,
        live=True,
    )
    assert should is False
    assert reason is not None and "blocked" in reason


def test_tiingo_deferred_outside_us_rth_when_bootstrapped():
    store = MagicMock()
    store.get_settings_dict.return_value = {}
    spy = next(a for a in list_target_assets() if a.db_symbol == "NVDA")
    # Wednesday 2026-09-09 02:00 UTC = Monday night / closed US session
    closed = datetime(2026, 9, 9, 2, 0, tzinfo=timezone.utc)
    should, reason = _should_poll_asset(
        store,
        spy,
        now=closed,
        stored=500,
        force_bootstrap=False,
        live=True,
    )
    assert should is False
    assert reason is not None and "closed" in reason.lower()


def test_bootstrap_not_deferred_on_live_path():
    store = MagicMock()
    store.get_settings_dict.return_value = {}
    btc = next(a for a in list_target_assets() if a.db_symbol == "BTCUSD")
    should, reason = _should_poll_asset(
        store,
        btc,
        now=datetime.now(timezone.utc),
        stored=50,
        force_bootstrap=True,
        live=True,
    )
    assert should is True
    assert reason is None


def test_bootstrap_allowed_on_bulk_path():
    store = MagicMock()
    store.get_settings_dict.return_value = {}
    eth = next(a for a in list_target_assets() if a.db_symbol == "ETHUSD")
    should, reason = _should_poll_asset(
        store,
        eth,
        now=datetime.now(timezone.utc),
        stored=0,
        force_bootstrap=True,
        live=False,
    )
    assert should is True
    assert reason is None
