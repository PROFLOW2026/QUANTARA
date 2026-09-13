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
    now_mono = __import__("time").monotonic()
    persister._last_persist_mono["BTCUSD"] = now_mono
    persister.maybe_persist("BTCUSD", Decimal("100"), datetime.now(timezone.utc), source="test")
    assert persister.writes == 0


def test_default_persist_interval_is_fifteen_seconds(monkeypatch):
    monkeypatch.delenv("STREAM_MARK_PERSIST_SEC", raising=False)
    import importlib

    import quantara_engine.market_data.streaming.mark_persist as mp

    importlib.reload(mp)
    assert mp.PERSIST_MIN_INTERVAL_SEC == 15.0


def test_hub_get_entry():
    hub = LiveMarkHub()
    now = datetime.now(timezone.utc)
    hub.update("ETHUSD", Decimal("2000"), now, source="test")
    entry = hub.get_entry("ETHUSD")
    assert entry is not None
    assert entry.price == Decimal("2000")


def test_hub_subscriber_cap():
    async def _run() -> None:
        hub = LiveMarkHub()
        loop = asyncio.get_running_loop()
        queues = [hub.subscribe(loop, max_subscribers=2) for _ in range(3)]
        assert queues[0] is not None
        assert queues[1] is not None
        assert queues[2] is None

    asyncio.run(_run())


def test_deliver_payload_uses_subscriber_loop():
    async def _run() -> None:
        hub = LiveMarkHub()
        loop = asyncio.get_running_loop()
        queue = hub.subscribe(loop)
        assert queue is not None
        sub = next(iter(hub._subscribers))
        payload = {"type": "marks", "marks": {"BTCUSD": {"price": 100.0}}, "ts": "now"}
        hub._deliver_payload(payload, [sub])
        received = await asyncio.wait_for(queue.get(), timeout=1.0)
        assert received == payload

    asyncio.run(_run())


def test_broadcast_loop_does_not_deadlock_on_dirty_marks():
    async def _run() -> None:
        hub = LiveMarkHub(broadcast_interval_sec=0.01)
        hub.bind_loop(asyncio.get_running_loop())
        now = datetime.now(timezone.utc)
        hub.update("BTCUSD", Decimal("100"), now, source="test")
        await asyncio.sleep(0.05)
        assert hub.get_entry("BTCUSD") is not None
        assert hub.stats()["broadcasts_sent"] >= 1
        hub._broadcast_task.cancel()

    asyncio.run(_run())
