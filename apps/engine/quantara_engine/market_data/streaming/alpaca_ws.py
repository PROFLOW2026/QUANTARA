"""Alpaca IEX WebSocket — US equity trades for LIVE_MARK during RTH."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Awaitable, Callable

from quantara_engine.core.config import settings
from quantara_engine.execution.crypto_mark_valuation import FAST_EQUITY_DB_SYMBOLS
from quantara_engine.market_data.sessions import is_us_equity_rth

logger = logging.getLogger(__name__)

ALPACA_IEX_WS_URL = "wss://stream.data.alpaca.markets/v2/iex"
EQUITY_SYMBOLS = tuple(sorted(FAST_EQUITY_DB_SYMBOLS))

TickHandler = Callable[[str, Decimal, datetime, str], Awaitable[None] | None]


def decode_alpaca_ws_payload(raw: str) -> list[dict[str, Any]]:
    """
    Alpaca v2 always sends JSON arrays (control messages length 1; trades may batch).

    Returns individual event dicts; malformed frames yield an empty list.
    """
    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError:
        logger.debug("Alpaca WS skipped non-JSON frame: %r", raw[:200])
        return []

    if isinstance(decoded, dict):
        return [decoded]
    if isinstance(decoded, list):
        events: list[dict[str, Any]] = []
        for item in decoded:
            if isinstance(item, dict):
                events.append(item)
            else:
                logger.warning(
                    "Alpaca WS skipped non-dict array element: type=%s sample=%r",
                    type(item).__name__,
                    item,
                )
        return events

    logger.warning(
        "Alpaca WS skipped unexpected payload type=%s sample=%r",
        type(decoded).__name__,
        decoded,
    )
    return []


def _parse_trade_timestamp(ts_raw: Any) -> datetime:
    if not ts_raw:
        return datetime.now(timezone.utc)
    ts_str = str(ts_raw).replace("Z", "+00:00")
    if "." in ts_str and len(ts_str.split(".")[-1]) > 6:
        ts_str = ts_str[:26] + "+00:00"
    return datetime.fromisoformat(ts_str)


async def _dispatch_trade(msg: dict[str, Any], on_tick: TickHandler) -> None:
    sym = msg.get("S")
    if sym not in FAST_EQUITY_DB_SYMBOLS:
        return
    price_raw = msg.get("p")
    if price_raw is None:
        return
    at = _parse_trade_timestamp(msg.get("t"))
    price = Decimal(str(price_raw))
    result = on_tick(sym, price, at, "alpaca_ws")
    if asyncio.iscoroutine(result):
        await result


async def _handle_alpaca_event(msg: dict[str, Any], on_tick: TickHandler) -> bool:
    """
    Process one Alpaca WS event.

    Returns True when authentication succeeded.
    """
    msg_type = msg.get("T")
    if msg_type == "error":
        code = msg.get("code")
        err_msg = msg.get("msg") or msg.get("message") or "unknown"
        logger.error("Alpaca WS error event: code=%s msg=%s", code, err_msg)
        return False
    if msg_type == "success" and msg.get("msg") == "authenticated":
        return True
    if msg_type == "subscription":
        logger.debug("Alpaca WS subscription ack: %s", msg)
        return False
    if msg_type == "t":
        await _dispatch_trade(msg, on_tick)
    return False


async def run_alpaca_iex_trade_stream(
    on_tick: TickHandler,
    *,
    should_run: Callable[[], bool] | None = None,
    on_status: Callable[[str], None] | None = None,
) -> None:
    key = settings.alpaca_api_key_id.strip()
    secret = settings.alpaca_api_secret_key.strip()
    if not key or not secret:
        logger.info("Alpaca WS skipped — credentials not configured")
        if on_status:
            on_status("skipped")
        while should_run is None or should_run():
            await asyncio.sleep(60)
        return

    import websockets

    def _set_status(status: str) -> None:
        if on_status:
            on_status(status)

    backoff = 1.0
    while should_run is None or should_run():
        now = datetime.now(timezone.utc)
        if not is_us_equity_rth(now):
            _set_status("outside_rth")
            await asyncio.sleep(30)
            continue

        _set_status("connecting")
        try:
            async with websockets.connect(
                ALPACA_IEX_WS_URL,
                ping_interval=20,
                ping_timeout=20,
                close_timeout=5,
                open_timeout=15,
            ) as ws:
                backoff = 1.0
                await ws.send(json.dumps({"action": "auth", "key": key, "secret": secret}))
                auth_ok = False
                for _ in range(5):
                    raw = await asyncio.wait_for(ws.recv(), timeout=10)
                    for msg in decode_alpaca_ws_payload(raw):
                        if await _handle_alpaca_event(msg, on_tick):
                            auth_ok = True
                    if auth_ok:
                        break
                if not auth_ok:
                    raise RuntimeError("Alpaca WS authentication failed")

                await ws.send(
                    json.dumps({"action": "subscribe", "trades": list(EQUITY_SYMBOLS)})
                )
                logger.info("Alpaca IEX WS subscribed: %s", EQUITY_SYMBOLS)
                _set_status("connected")

                async for raw in ws:
                    if should_run is not None and not should_run():
                        break
                    if not is_us_equity_rth(datetime.now(timezone.utc)):
                        break
                    for msg in decode_alpaca_ws_payload(raw):
                        try:
                            await _handle_alpaca_event(msg, on_tick)
                        except Exception:
                            logger.exception(
                                "Alpaca WS event handling failed: T=%s S=%s",
                                msg.get("T"),
                                msg.get("S"),
                            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            _set_status("reconnecting")
            logger.warning("Alpaca WS disconnected: %s — retry in %.1fs", exc, backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60.0)
