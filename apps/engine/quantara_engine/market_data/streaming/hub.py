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


@dataclass(frozen=True)
class _Subscriber:
    loop: asyncio.AbstractEventLoop
    queue: asyncio.Queue[dict[str, Any]]


@dataclass
class LiveMarkHub:
    """Thread-safe latest marks; SSE broadcast coalesced to ~1 Hz."""

    broadcast_interval_sec: float = 1.0
    _lock: threading.RLock = field(default_factory=threading.RLock, init=False, repr=False)
    _marks: dict[str, LiveMarkEntry] = field(default_factory=dict, init=False, repr=False)
    _subscribers: set[_Subscriber] = field(default_factory=set, init=False, repr=False)
    _dirty: bool = field(default=False, init=False, repr=False)
    _loop: asyncio.AbstractEventLoop | None = field(default=None, init=False, repr=False)
    _broadcast_task: asyncio.Task | None = field(default=None, init=False, repr=False)
    _started_at: float | None = field(default=None, init=False, repr=False)
    _messages_received: int = field(default=0, init=False, repr=False)
    _broadcasts_sent: int = field(default=0, init=False, repr=False)

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Bind the stream thread loop once for coalesced fan-out."""
        with self._lock:
            if self._loop is not None and self._loop is not loop:
                return
            self._loop = loop
            if self._broadcast_task is None or self._broadcast_task.done():
                self._broadcast_task = loop.create_task(self._broadcast_loop())
                self._started_at = time.monotonic()

    def reset_runtime_state(self) -> None:
        """Clear subscribers/broadcast state on Engine startup/shutdown."""
        with self._lock:
            task = self._broadcast_task
            self._broadcast_task = None
            self._loop = None
            self._subscribers.clear()
            self._dirty = False
        if task is not None and not task.done():
            task.cancel()

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

    def get_entry(self, db_symbol: str) -> LiveMarkEntry | None:
        sym = normalize_db_symbol(db_symbol)
        with self._lock:
            return self._marks.get(sym)

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

    def subscribe(
        self,
        loop: asyncio.AbstractEventLoop,
        *,
        max_subscribers: int = 8,
    ) -> asyncio.Queue[dict[str, Any]] | None:
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=4)
        sub = _Subscriber(loop=loop, queue=q)
        with self._lock:
            if len(self._subscribers) >= max_subscribers:
                return None
            self._subscribers.add(sub)
        return q

    def unsubscribe(self, q: asyncio.Queue[dict[str, Any]]) -> None:
        with self._lock:
            self._subscribers = {s for s in self._subscribers if s.queue is not q}

    def _deliver_payload(self, payload: dict[str, Any], subs: list[_Subscriber]) -> None:
        for sub in subs:
            loop = sub.loop
            queue = sub.queue

            def _put(q: asyncio.Queue[dict[str, Any]] = queue, data: dict[str, Any] = payload) -> None:
                try:
                    q.put_nowait(data)
                except asyncio.QueueFull:
                    try:
                        q.get_nowait()
                    except asyncio.QueueEmpty:
                        pass
                    try:
                        q.put_nowait(data)
                    except asyncio.QueueFull:
                        pass

            try:
                if loop.is_closed():
                    continue
                loop.call_soon_threadsafe(_put)
            except RuntimeError:
                continue

    async def _broadcast_loop(self) -> None:
        while True:
            await asyncio.sleep(self.broadcast_interval_sec)
            payload: dict[str, Any] | None = None
            subs: list[_Subscriber] = []
            with self._lock:
                if self._dirty and self._marks:
                    marks = {sym: entry.to_dict() for sym, entry in self._marks.items()}
                    payload = {
                        "type": "marks",
                        "marks": marks,
                        "ts": datetime.now(timezone.utc).isoformat(),
                    }
                    self._dirty = False
                    self._broadcasts_sent += 1
                subs = list(self._subscribers)
            if payload is None:
                continue
            self._deliver_payload(payload, subs)

    def note_provider_message(self) -> None:
        with self._lock:
            self._messages_received += 1


_HUB = LiveMarkHub()


def get_live_mark_hub() -> LiveMarkHub:
    return _HUB


def reset_live_mark_hub() -> None:
    _HUB.reset_runtime_state()
