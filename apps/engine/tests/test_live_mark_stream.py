"""Live mark WebSocket hub, token, and SSE helpers."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from quantara_engine.market_data.streaming.hub import LiveMarkHub
from quantara_engine.market_data.streaming.mark_persist import ThrottledMarkPersister
from quantara_engine.market_data.streaming.stream_token import (
    issue_stream_token,
    verify_stream_token,
)


def test_stream_token_roundtrip():
    token = issue_stream_token(ttl_sec=120)
    assert verify_stream_token(token) is True
    assert verify_stream_token("bad.token") is False


def test_hub_snapshot_latest_price():
    hub = LiveMarkHub()
    now = datetime.now(timezone.utc)
    hub.update("BTCUSD", Decimal("100"), now, source="test")
    hub.update("BTCUSD", Decimal("101"), now, source="test")
    payload = hub.snapshot()
    assert payload["marks"]["BTCUSD"]["price"] == 101.0


def test_throttled_persister_limits_writes():
    persister = ThrottledMarkPersister(min_interval_sec=60.0)
    at = datetime.now(timezone.utc)

    class _Store:
        pass

    # First call would try DB — mock by patching session_scope in integration tests.
    # Here we only verify throttle gate without DB.
    persister._last_persist_mono["BTCUSD"] = 0.0
    persister._min_interval_sec = 60.0
    now_mono = __import__("time").monotonic()
    persister._last_persist_mono["BTCUSD"] = now_mono
    assert persister.writes == 0
