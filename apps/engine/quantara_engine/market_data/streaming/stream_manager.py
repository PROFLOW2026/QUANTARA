"""Background provider WebSocket clients (Engine process)."""

from __future__ import annotations

import asyncio
import logging
import os
import threading
from datetime import datetime
from decimal import Decimal

from quantara_engine.core.config import settings
from quantara_engine.market_data.streaming.alpaca_ws import run_alpaca_iex_trade_stream
from quantara_engine.market_data.streaming.coinbase_ws import run_coinbase_ticker_stream
from quantara_engine.market_data.streaming.hub import get_live_mark_hub
from quantara_engine.market_data.streaming.mark_persist import get_mark_persister

logger = logging.getLogger(__name__)

_thread: threading.Thread | None = None
_loop: asyncio.AbstractEventLoop | None = None
_running = False
_status: dict = {"enabled": False, "coinbase": "stopped", "alpaca": "stopped"}


def _streaming_enabled() -> bool:
    flag = os.environ.get("MARKET_DATA_STREAMING", "1").strip().lower()
    if flag in ("0", "false", "no", "off"):
        return False
    if settings.market_data_provider.strip().lower() == "mock":
        return False
    return True


def stream_manager_status() -> dict:
    hub = get_live_mark_hub()
    persister = get_mark_persister()
    return {
        **_status,
        "hub": hub.stats(),
        "persist_writes": persister.writes,
    }


async def _on_tick(db_symbol: str, price: Decimal, at: datetime, source: str) -> None:
    hub = get_live_mark_hub()
    if hub.update(db_symbol, price, at, source=source):
        get_mark_persister().maybe_persist(db_symbol, price, at, source=source)


async def _run_streams() -> None:
    global _status
    hub = get_live_mark_hub()
    hub.bind_loop(asyncio.get_running_loop())
    _status["coinbase"] = "connecting"
    _status["alpaca"] = "connecting"

    await asyncio.gather(
        run_coinbase_ticker_stream(_on_tick, should_run=lambda: _running),
        run_alpaca_iex_trade_stream(_on_tick, should_run=lambda: _running),
    )


def _thread_main() -> None:
    global _loop, _running, _status
    _loop = asyncio.new_event_loop()
    asyncio.set_event_loop(_loop)
    _running = True
    _status["enabled"] = True
    try:
        _loop.run_until_complete(_run_streams())
    except Exception:
        logger.exception("Stream manager loop exited")
    finally:
        _running = False
        _status["coinbase"] = "stopped"
        _status["alpaca"] = "stopped"
        _loop.close()


def start_stream_manager() -> None:
    global _thread
    if not _streaming_enabled():
        logger.info("Live mark streaming disabled (MARKET_DATA_STREAMING or mock provider)")
        _status["enabled"] = False
        return
    if _thread is not None and _thread.is_alive():
        return
    _thread = threading.Thread(target=_thread_main, name="live-mark-streams", daemon=True)
    _thread.start()
    logger.info("Live mark stream manager started")


def stop_stream_manager() -> None:
    global _running, _thread
    _running = False
    if _loop is not None and _loop.is_running():
        _loop.call_soon_threadsafe(lambda: None)
    if _thread is not None:
        _thread.join(timeout=5)
    _thread = None
