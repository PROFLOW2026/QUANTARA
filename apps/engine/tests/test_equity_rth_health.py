"""RTH session-open feed health vs strategy timeframe readiness."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from quantara_engine.domain.types import Candle
from quantara_engine.market_data.aggregation import aggregate_from_1m
from quantara_engine.market_data.equity_rth_health import (
    FEED_STATUS_RTH_WARMUP,
    STRATEGY_STATUS_WARMUP,
    classify_equity_feed_health,
    in_rth_warmup_phase,
    is_equity_strategy_candle_eligible,
    resolve_equity_session_stale,
)
from quantara_engine.market_data.registry import get_asset
from quantara_engine.market_data.strategy_freshness_health import (
    aggregate_market_health,
    classify_strategy_candle_health,
)
from quantara_workers.jobs.fetch_data import _asset_worker_feed_status, _try_local_1m_canonical

ET = ZoneInfo("America/New_York")


def _rth(day: int, hour: int, minute: int) -> datetime:
    """US RTH wall time on 2026-09-15 (Monday) in UTC."""
    local = datetime(2026, 9, day, hour, minute, tzinfo=ET)
    return local.astimezone(timezone.utc)


def _1m(ts: datetime, close: str = "100") -> Candle:
    return Candle(
        instrument_id="inst-amd",
        timeframe="1m",
        timestamp=ts,
        open=Decimal(close),
        high=Decimal(close),
        low=Decimal(close),
        close=Decimal(close),
        source="alpaca",
        is_complete=True,
    )


def test_a_rth_warmup_feed_not_data_error_strategy_blocks_premarket_5m():
    """16:31 RTH — fresh 1m, no completed RTH 5m yet."""
    now = _rth(15, 9, 31)
    asset = get_asset("AMD")
    assert asset is not None
    last_1m = now - timedelta(minutes=1)
    premarket_5m = _rth(15, 9, 15)

    feed = classify_equity_feed_health(last_1m=last_1m, last_5m=premarket_5m, now=now)
    assert feed["feed_status"] == FEED_STATUS_RTH_WARMUP
    assert feed["ui_data_error"] is False
    assert feed["dashboard_status"] == FEED_STATUS_RTH_WARMUP
    assert in_rth_warmup_phase(now)

    stale, status = resolve_equity_session_stale(asset, premarket_5m, now, last_1m=last_1m)
    assert stale is False
    assert status == FEED_STATUS_RTH_WARMUP

    eligible, reason = is_equity_strategy_candle_eligible(asset, premarket_5m, now)
    assert eligible is False
    assert reason == STRATEGY_STATUS_WARMUP
    assert "stale_data" not in reason


def test_b_rth_after_first_bucket_healthy_and_strategy_eligible():
    """16:36 RTH — completed 09:30 ET 5m exists."""
    now = _rth(15, 9, 36)
    asset = get_asset("NVDA")
    assert asset is not None
    last_1m = now - timedelta(minutes=1)
    rth_5m = _rth(15, 9, 30)

    feed = classify_equity_feed_health(last_1m=last_1m, last_5m=rth_5m, now=now)
    assert feed["feed_status"] == "healthy"
    assert feed["ui_data_error"] is False

    eligible, reason = is_equity_strategy_candle_eligible(asset, rth_5m, now)
    assert eligible is True
    assert reason == "eligible"


def test_c_missing_rth_5m_after_due_is_real_data_error():
    """16:36 RTH — fresh 1m but no completed RTH 5m when due."""
    now = _rth(15, 9, 36)
    asset = get_asset("AMD")
    assert asset is not None
    last_1m = now - timedelta(minutes=1)
    prior_session_5m = _rth(14, 15, 50)

    feed = classify_equity_feed_health(last_1m=last_1m, last_5m=prior_session_5m, now=now)
    assert feed["ui_data_error"] is True
    assert feed["feed_status"] == "data_error"

    eligible, reason = is_equity_strategy_candle_eligible(asset, prior_session_5m, now)
    assert eligible is False
    assert reason.startswith("stale_data")


def test_d_stale_1m_during_rth_is_data_error():
    now = _rth(15, 9, 40)
    asset = get_asset("COIN")
    assert asset is not None
    stale_1m = now - timedelta(minutes=20)
    rth_5m = _rth(15, 9, 30)

    feed = classify_equity_feed_health(last_1m=stale_1m, last_5m=rth_5m, now=now)
    assert feed["ui_data_error"] is True
    assert feed["one_m_fresh"] is False


def test_e_per_symbol_health_independent():
    now = _rth(15, 9, 36)
    fresh_1m = now - timedelta(minutes=1)
    rth_5m = _rth(15, 9, 30)
    stale_5m = _rth(14, 15, 50)

    nvda_feed = classify_equity_feed_health(last_1m=fresh_1m, last_5m=rth_5m, now=now)
    amd_feed = classify_equity_feed_health(last_1m=fresh_1m, last_5m=stale_5m, now=now)

    assert nvda_feed["feed_status"] == "healthy"
    assert amd_feed["feed_status"] == "data_error"

    summary = aggregate_market_health(
        {
            "NVDA": classify_strategy_candle_health(get_asset("NVDA"), rth_5m, now),
            "AMD": classify_strategy_candle_health(get_asset("AMD"), stale_5m, now),
        }
    )
    assert "AMD" in summary["stale_open_assets"]
    assert "NVDA" not in summary["stale_open_assets"]


def test_f_warmup_feed_while_1m_fresh_not_strategy_stale():
    now = _rth(15, 9, 32)
    asset = get_asset("AMD")
    assert asset is not None
    last_1m = now - timedelta(minutes=1)

    row = classify_strategy_candle_health(asset, None, now, last_1m=last_1m)
    assert row["classification"] == "rth_warmup"
    assert row["one_m_fresh"] is True

    summary = aggregate_market_health({"AMD": row})
    assert "AMD" not in summary["stale_open_assets"]


def test_g_local_1m_derives_first_rth_5m_bucket():
    open_5m = _rth(15, 9, 30)
    bars = [_1m(open_5m + timedelta(minutes=i), str(100 + i)) for i in range(5)]
    derived = aggregate_from_1m(bars, session_mode="us_rth")
    assert len(derived) == 1
    assert derived[0].timeframe == "5m"
    assert derived[0].timestamp == open_5m


def test_h_try_local_1m_does_not_treat_premarket_5m_as_fresh_during_rth():
    now = _rth(15, 9, 36)
    asset = get_asset("AMD")
    assert asset is not None
    store = MagicMock()
    store.latest_candle_timestamp.return_value = _rth(15, 9, 15)
    store.count_candles.return_value = 500
    instrument = MagicMock(id="inst-amd")

    with patch(
        "quantara_workers.jobs.fetch_data.derive_higher_from_1m",
        return_value=(0, 0),
    ):
        _count, _higher, fresh = _try_local_1m_canonical(store, instrument, asset, now)

    assert fresh is False


def test_worker_feed_status_per_symbol_warmup():
    now = _rth(15, 9, 31)
    asset = get_asset("TSLA")
    assert asset is not None
    store = MagicMock()
    store.latest_candle_timestamp.side_effect = [
        now - timedelta(minutes=1),
    ]
    instrument = MagicMock(id="inst-tsla")
    status = _asset_worker_feed_status(
        store,
        instrument,
        asset,
        now,
        latest_5m=_rth(15, 9, 15),
        stored=500,
        count=0,
        live_provider="alpaca",
        configured_primary="alpaca",
    )
    assert status["status"] == FEED_STATUS_RTH_WARMUP
    assert status["feed_status"] == FEED_STATUS_RTH_WARMUP
