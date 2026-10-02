"""Multi-market Live Sim — session gates must be asset-class aware (not US-RTH global)."""

from __future__ import annotations

from datetime import datetime, timezone

from quantara_engine.market_data.registry import get_asset
from quantara_engine.market_data.sessions import is_us_equity_rth
from quantara_engine.market_data.strategy_freshness_health import is_strategy_candle_eligible


def test_crypto_eligible_when_us_equity_session_closed():
    """Sunday evening UTC: US RTH closed; crypto 24/7 should remain eligible with fresh marks."""
    now = datetime(2026, 10, 4, 20, 0, tzinfo=timezone.utc)  # Saturday UTC — US closed
    assert not is_us_equity_rth(now)
    asset = get_asset("BTCUSD")
    last = now.replace(minute=0, second=0, microsecond=0)
    ok, reason = is_strategy_candle_eligible(asset, last, now, timeframe="15m")
    assert ok, reason


def test_us_equity_not_eligible_when_session_closed():
    now = datetime(2026, 10, 4, 20, 0, tzinfo=timezone.utc)
    asset = get_asset("AMD")
    last = datetime(2026, 10, 3, 19, 50, tzinfo=timezone.utc)
    ok, reason = is_strategy_candle_eligible(asset, last, now, timeframe="5m")
    assert not ok
    assert reason == "session_closed"
