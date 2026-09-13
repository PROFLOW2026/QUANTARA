"""Alpaca IEX WebSocket — US equity trades for LIVE_MARK during RTH."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Awaitable, Callable

from quantara_engine.core.config import settings
from quantara_engine.execution.crypto_mark_valuation import FAST_EQUITY_DB_SYMBOLS
from quantara_engine.market_data.sessions import is_us_equity_rth

logger = logging.getLogger(__name__)

ALPACA_IEX_WS_URL = "wss://stream.data.alpaca.markets/v2/iex"
EQUITY_SYMBOLS = tuple(sorted(FAST_EQUITY_DB_SYMBOLS))

TickHandler = Callable[[str, Decimal, datetime, str], Awaitable[None] | None]


async def run_alpaca_iex_trade_stream(
    on_tick: TickHandler,
    *,
    should_run: Callable[[], bool] | None = None,
) -> None:
    key = settings.alpaca_api_key_id.strip()
    secret = settings.alpaca_api_secret_key.strip()
    if not key or not secret:
        logger.info("Alpaca WS skipped — credentials not configured")
        while should_run is None or should_run():
            await asyncio.sleep(60)
        return

    import websockets

    backoff = 1.0
    while should_run is None or should_run():
        now = datetime.now(timezone.utc)
        if not is_us_equity_rth(now):
            await asyncio.sleep(30)
            continue

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
                    msg = json.loads(raw)
                    if msg.get("T") == "success" and msg.get("msg") == "authenticated":
                        auth_ok = True
                        break
                if not auth_ok:
                    raise RuntimeError("Alpaca WS authentication failed")

                await ws.send(
                    json.dumps({"action": "subscribe", "trades": list(EQUITY_SYMBOLS)})
                )
                logger.info("Alpaca IEX WS subscribed: %s", EQUITY_SYMBOLS)

                async for raw in ws:
                    if should_run is not None and not should_run():
                        break
                    if not is_us_equity_rth(datetime.now(timezone.utc)):
                        break
                    try:
                        msg = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    if msg.get("T") != "t":
                        continue
                    sym = msg.get("S")
                    if sym not in FAST_EQUITY_DB_SYMBOLS:
                        continue
                    price_raw = msg.get("p")
                    if price_raw is None:
                        continue
                    ts_raw = msg.get("t")
                    if ts_raw:
                        # Alpaca RFC3339 nano
                        ts_str = str(ts_raw).replace("Z", "+00:00")
                        if "." in ts_str and len(ts_str.split(".")[-1]) > 6:
                            ts_str = ts_str[:26] + "+00:00"
                        at = datetime.fromisoformat(ts_str)
                    else:
                        at = datetime.now(timezone.utc)
                    price = Decimal(str(price_raw))
                    result = on_tick(sym, price, at, "alpaca_ws")
                    if asyncio.iscoroutine(result):
                        await result
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("Alpaca WS disconnected: %s — retry in %.1fs", exc, backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60.0)
