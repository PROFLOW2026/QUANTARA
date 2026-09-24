"""Alpaca IEX WebSocket payload parsing and stream handling."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

from quantara_engine.market_data.streaming.alpaca_ws import (
    _handle_alpaca_event,
    decode_alpaca_ws_payload,
    run_alpaca_iex_trade_stream,
)


def test_decode_dict_payload():
    events = decode_alpaca_ws_payload('{"T":"success","msg":"authenticated"}')
    assert events == [{"T": "success", "msg": "authenticated"}]


def test_decode_list_payload_multiple_trades():
    raw = json.dumps(
        [
            {"T": "t", "S": "NVDA", "p": 100.5, "t": "2026-09-24T15:00:00Z"},
            {"T": "t", "S": "TSLA", "p": 200.1, "t": "2026-09-24T15:00:01Z"},
        ]
    )
    events = decode_alpaca_ws_payload(raw)
    assert len(events) == 2
    assert events[0]["S"] == "NVDA"
    assert events[1]["S"] == "TSLA"


def test_decode_auth_subscription_list_payload():
    raw = '[{"T":"success","msg":"authenticated"}]'
    assert decode_alpaca_ws_payload(raw)[0]["msg"] == "authenticated"

    sub = '[{"T":"subscription","trades":["NVDA","TSLA"]}]'
    assert decode_alpaca_ws_payload(sub)[0]["T"] == "subscription"


def test_decode_malformed_payload_types():
    assert decode_alpaca_ws_payload('"hello"') == []
    assert decode_alpaca_ws_payload("[1, 2]") == []
    assert decode_alpaca_ws_payload("not-json") == []


def test_handle_trade_dict_processed():
    ticks: list[tuple[str, Decimal]] = []

    async def on_tick(sym, price, at, source):
        ticks.append((sym, price))

    async def _run() -> None:
        await _handle_alpaca_event(
            {"T": "t", "S": "NVDA", "p": 123.45, "t": "2026-09-24T15:00:00Z"},
            on_tick,
        )

    asyncio.run(_run())
    assert ticks == [("NVDA", Decimal("123.45"))]


def test_handle_list_payload_all_events_processed():
    ticks: list[str] = []

    async def on_tick(sym, price, at, source):
        ticks.append(sym)

    async def _run() -> None:
        raw = json.dumps(
            [
                {"T": "t", "S": "NVDA", "p": 1, "t": "2026-09-24T15:00:00Z"},
                {"T": "t", "S": "AMD", "p": 2, "t": "2026-09-24T15:00:01Z"},
            ]
        )
        for msg in decode_alpaca_ws_payload(raw):
            await _handle_alpaca_event(msg, on_tick)

    asyncio.run(_run())
    assert ticks == ["NVDA", "AMD"]


def test_auth_list_payload_no_crash():
    auth = {"ok": False}

    async def on_tick(*_args):
        pass

    async def _run() -> None:
        for msg in decode_alpaca_ws_payload('[{"T":"success","msg":"authenticated"}]'):
            if await _handle_alpaca_event(msg, on_tick):
                auth["ok"] = True

    asyncio.run(_run())
    assert auth["ok"] is True


def test_error_event_logged_not_raised(caplog):
    async def on_tick(*_args):
        pass

    async def _run() -> None:
        with caplog.at_level("ERROR"):
            ok = await _handle_alpaca_event(
                {"T": "error", "code": 401, "msg": "auth failed"},
                on_tick,
            )
        assert ok is False

    asyncio.run(_run())
    assert any("Alpaca WS error event" in rec.message for rec in caplog.records)


def test_malformed_event_skipped_without_crashing_stream():
    calls = {"n": 0}

    async def on_tick(*_args):
        calls["n"] += 1

    async def _run() -> None:
        for msg in decode_alpaca_ws_payload("[42]"):
            await _handle_alpaca_event(msg, on_tick)

    asyncio.run(_run())
    assert calls["n"] == 0


def test_successful_stream_no_reconnect_on_list_trades():
    """List-shaped trade batches must not trip msg.get and force reconnect."""
    received: list[str] = []
    statuses: list[str] = []

    async def on_tick(sym, price, at, source):
        received.append(sym)

    trade_frame = json.dumps(
        [{"T": "t", "S": "NVDA", "p": 50.0, "t": "2026-09-24T19:30:00Z"}]
    )
    auth_frame = json.dumps([{"T": "success", "msg": "authenticated"}])

    class FakeWS:
        def __init__(self):
            self._sent = []
            self._recv_frames = [auth_frame]
            self._stream_frames = [trade_frame]

        async def recv(self):
            if self._recv_frames:
                return self._recv_frames.pop(0)
            raise asyncio.TimeoutError

        async def send(self, payload):
            self._sent.append(payload)

        def __aiter__(self):
            return self

        async def __anext__(self):
            if self._stream_frames:
                return self._stream_frames.pop(0)
            raise StopAsyncIteration

    async def _run() -> None:
        fake_ws = FakeWS()
        connect_cm = MagicMock()
        connect_cm.__aenter__ = AsyncMock(return_value=fake_ws)
        connect_cm.__aexit__ = AsyncMock(return_value=False)

        with patch("websockets.connect", return_value=connect_cm):
            with patch(
                "quantara_engine.market_data.streaming.alpaca_ws.settings"
            ) as mock_settings:
                mock_settings.alpaca_api_key_id = "key"
                mock_settings.alpaca_api_secret_key = "secret"
                with patch(
                    "quantara_engine.market_data.streaming.alpaca_ws.is_us_equity_rth",
                    return_value=True,
                ):
                    await asyncio.wait_for(
                        run_alpaca_iex_trade_stream(
                            on_tick,
                            should_run=lambda: len(received) < 1,
                            on_status=statuses.append,
                        ),
                        timeout=2.0,
                    )

    asyncio.run(_run())
    assert received == ["NVDA"]
    assert "connected" in statuses
    assert statuses.count("reconnecting") == 0


def test_equity_feed_healthy_while_live_stream_down_is_separate():
    """Strategy/protection feed health must not require WS marks."""
    from datetime import timedelta
    from zoneinfo import ZoneInfo

    from quantara_engine.market_data.equity_rth_health import classify_equity_feed_health

    et = ZoneInfo("America/New_York")
    now = datetime(2026, 9, 15, 9, 36, tzinfo=et).astimezone(timezone.utc)
    fresh_1m = now - timedelta(minutes=1)
    rth_5m = datetime(2026, 9, 15, 9, 30, tzinfo=et).astimezone(timezone.utc)

    feed = classify_equity_feed_health(last_1m=fresh_1m, last_5m=rth_5m, now=now)
    assert feed["ui_data_error"] is False
    assert feed["feed_status"] == "healthy"
