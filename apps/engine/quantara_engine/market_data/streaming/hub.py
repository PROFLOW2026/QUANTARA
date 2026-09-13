"""In-memory canonical LIVE_MARK hub — coalesced for SSE subscribers."""

from __future__ import annotations

import asyncio
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from quantara_engine.market_data.symbols import normalize_db_symbol


@dataclass(frozen=True)
class LiveMarkEntry:
    db_symbol: str
    price: Decimal
    at: datetime
    source: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "db_symbol": self.db_symbol,
            "price": float(self.price),
            "at": self.at.isoformat(),
            "source": self.source,
        }


@dataclass
class LiveMarkHub:
    """Thread-safe latest marks; SSE broadcast coalesced to ~1 Hz."""

    broadcast_interval_sec: float = 1.0
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)
    _marks: dict[str, LiveMarkEntry] = field(default_factory=dict, init=False, repr=False)
    _subscribers: set[asyncio.Queue[dict[str, Any]]] = field(default_factory=set, init=False, repr=False)
    _dirty: bool = field(default=False, init=False, repr=False)
    _loop: asyncio.AbstractEventLoop | None = field(default=None, init=False, repr=False)
    _broadcast_task: asyncio.Task | None = field(default=None, init=False, repr=False)
    _started_at: float | None = field(default=None, init=False, repr=False)
    _messages_received: int = field(default=0, init=False, repr=False)
    _broadcasts_sent: int = field(default=0, init=False, repr=False)

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        with self._lock:
            self._loop = loop
            if self._broadcast_task is None or self._broadcast_task.done():
                self._broadcast_task = loop.create_task(self._broadcast_loop())
                self._started_at = time.monotonic()

    def update(
        self,
        db_symbol: str,
        price: Decimal,
        at: datetime,
        *,
        source: str,
    ) -> bool:
        sym = normalize_db_symbol(db_symbol)
        at = at.astimezone(timezone.utc) if at.tzinfo else at.replace(tzinfo=timezone.utc)
        with self._lock:
            prev = self._marks.get(sym)
            if prev and prev.at >= at and prev.price == price:
                return False
            self._marks[sym] = LiveMarkEntry(sym, price, at, source)
            self._dirty = True
            self._messages_received += 1
            return True

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            marks = {sym: entry.to_dict() for sym, entry in self._marks.items()}
        return {"type": "marks", "marks": marks, "ts": datetime.now(timezone.utc).isoformat()}

    def stats(self) -> dict[str, Any]:
        with self._lock:
            return {
                "messages_received": self._messages_received,
                "broadcasts_sent": self._broadcasts_sent,
                "symbols": len(self._marks),
                "subscribers": len(self._subscribers),
                "uptime_sec": round(time.monotonic() - self._started_at, 1)
                if self._started_at
                else 0,
            }

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=4)
        with self._lock:
            self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[dict[str, Any]]) -> None:
        with self._lock:
            self._subscribers.discard(q)

    async def _broadcast_loop(self) -> None:
        while True:
            await asyncio.sleep(self.broadcast_interval_sec)
            payload: dict[str, Any] | None = None
            with self._lock:
                if self._dirty and self._marks:
                    payload = self.snapshot()
                    self._dirty = False
                    self._broadcasts_sent += 1
                subs = list(self._subscribers)
            if payload is None:
                continue
            for q in subs:
                try:
                    q.put_nowait(payload)
                except asyncio.QueueFull:
                    try:
                        q.get_nowait()
                    except asyncio.QueueEmpty:
                        pass
                    try:
                        q.put_nowait(payload)
                    except asyncio.QueueFull:
                        pass

    def note_provider_message(self) -> None:
        with self._lock:
            self._messages_received += 1


_HUB = LiveMarkHub()


def get_live_mark_hub() -> LiveMarkHub:
    return _HUB
