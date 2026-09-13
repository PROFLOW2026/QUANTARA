"""Coinbase Exchange WebSocket — BTC/ETH ticker for LIVE_MARK."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Awaitable, Callable

logger = logging.getLogger(__name__)

COINBASE_WS_URL = "wss://ws-feed.exchange.coinbase.com"
COINBASE_PRODUCTS = ("BTC-USD", "ETH-USD")
PRODUCT_TO_DB = {"BTC-USD": "BTCUSD", "ETH-USD": "ETHUSD"}

TickHandler = Callable[[str, Decimal, datetime, str], Awaitable[None] | None]


async def run_coinbase_ticker_stream(
    on_tick: TickHandler,
    *,
    should_run: Callable[[], bool] | None = None,
) -> None:
    import websockets

    backoff = 1.0
    while should_run is None or should_run():
        try:
            async with websockets.connect(
                COINBASE_WS_URL,
                ping_interval=20,
                ping_timeout=20,
                close_timeout=5,
                open_timeout=15,
            ) as ws:
                backoff = 1.0
                await ws.send(
                    json.dumps(
                        {
                            "type": "subscribe",
                            "product_ids": list(COINBASE_PRODUCTS),
                            "channels": ["ticker"],
                        }
                    )
                )
                logger.info("Coinbase WS subscribed: %s", COINBASE_PRODUCTS)
                async for raw in ws:
                    if should_run is not None and not should_run():
                        break
                    try:
                        msg = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    if msg.get("type") != "ticker":
                        continue
                    product = msg.get("product_id")
                    db_sym = PRODUCT_TO_DB.get(product or "")
                    price_raw = msg.get("price")
                    if not db_sym or price_raw is None:
                        continue
                    ts_raw = msg.get("time")
                    if ts_raw:
                        at = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00"))
                    else:
                        at = datetime.now(timezone.utc)
                    price = Decimal(str(price_raw))
                    result = on_tick(db_sym, price, at, "coinbase_ws")
                    if asyncio.iscoroutine(result):
                        await result
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("Coinbase WS disconnected: %s — retry in %.1fs", exc, backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60.0)
