"""Throttled persistence of stream marks — avoids DB write explosion."""

from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime
from decimal import Decimal

from quantara_engine.db.session import session_scope
from quantara_engine.execution.crypto_mark_valuation import (
    FAST_CANONICAL_MARKS_KEY,
    apply_fast_1m_marks,
    load_fast_canonical_marks,
)
from quantara_engine.execution.equity_live_mark import apply_equity_ws_live_mark
from quantara_engine.market_data.symbols import normalize_db_symbol
from quantara_engine.persistence.store import TradingStore

logger = logging.getLogger(__name__)

def _default_persist_interval_sec() -> float:
    raw = os.environ.get("STREAM_MARK_PERSIST_SEC", "15").strip()
    try:
        value = float(raw)
    except ValueError:
        return 15.0
    return max(5.0, min(value, 120.0))


PERSIST_MIN_INTERVAL_SEC = _default_persist_interval_sec()


class ThrottledMarkPersister:
    def __init__(self, min_interval_sec: float | None = None) -> None:
        min_interval_sec = PERSIST_MIN_INTERVAL_SEC if min_interval_sec is None else min_interval_sec
        self._min_interval_sec = min_interval_sec
        self._lock = threading.Lock()
        self._last_persist_mono: dict[str, float] = {}
        self._writes = 0

    @property
    def writes(self) -> int:
        return self._writes

    def maybe_persist(
        self,
        db_symbol: str,
        price: Decimal,
        at: datetime,
        *,
        source: str,
    ) -> None:
        sym = normalize_db_symbol(db_symbol)
        now_mono = time.monotonic()
        with self._lock:
            last = self._last_persist_mono.get(sym, 0.0)
            if now_mono - last < self._min_interval_sec:
                return
            self._last_persist_mono[sym] = now_mono

        try:
            with session_scope() as session:
                store = TradingStore(session)
                if source.startswith("alpaca"):
                    apply_equity_ws_live_mark(store, sym, price, at)
                else:
                    apply_fast_1m_marks(store, {sym: (price, at)}, flush=False)
                    stored = load_fast_canonical_marks(store)
                    entry = dict(stored.get(sym) or {})
                    entry["source"] = source
                    stored[sym] = entry
                    store.update_settings(FAST_CANONICAL_MARKS_KEY, stored, flush=False)
                session.commit()
                self._writes += 1
        except Exception:
            logger.exception("Stream mark persist failed for %s", sym)


_PERSISTER = ThrottledMarkPersister()


def get_mark_persister() -> ThrottledMarkPersister:
    return _PERSISTER
