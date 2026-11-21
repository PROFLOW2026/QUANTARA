"""Persist fetched option chains through ``OptionsRepository.save_chain_snapshots(...)``.

The repository is owned by the Database module; this recorder is duck-typed against it and
adapts to its parameter naming (``chain`` vs. a list of ``OptionQuoteSnapshot`` rows) via
signature inspection, so either shape works. Fixture chains are NOT persisted unless
``record_fixture=True`` (synthetic data must not pollute real history).
"""

from __future__ import annotations

import inspect
import logging
import threading
import time
from datetime import datetime
from typing import Any, Callable, Optional

from quantara_options.contracts import DataFeedTier, OptionChainResponse, OptionQuoteSnapshot

logger = logging.getLogger(__name__)

_CHAIN_PARAM_NAMES = {"chain", "chain_response", "response", "option_chain", "options_chain"}
_ROWS_PARAM_NAMES = {"snapshots", "quotes", "contracts", "chain_snapshots", "rows", "items", "quote_snapshots"}


class SnapshotRecorder:
    def __init__(
        self,
        repository: Any,
        *,
        min_interval_seconds: float = 0.0,
        record_fixture: bool = False,
        skip_empty: bool = True,
        raise_on_error: bool = False,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._repo = repository
        self.min_interval_seconds = float(min_interval_seconds)
        self.record_fixture = record_fixture
        self.skip_empty = skip_empty
        self.raise_on_error = raise_on_error
        self._clock = clock
        self._last_saved: dict[str, float] = {}
        self._lock = threading.Lock()
        self.stats = {"recorded_chains": 0, "recorded_rows": 0, "skipped": 0, "errors": 0}
        self.last_error: Optional[str] = None

    def _invoke(self, chain: OptionChainResponse) -> int:
        fn = self._repo.save_chain_snapshots
        rows: list[OptionQuoteSnapshot] = list(chain.contracts)
        try:
            params = [p for p in inspect.signature(fn).parameters.values()
                      if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD, p.KEYWORD_ONLY)]
        except (TypeError, ValueError):
            params = []
        names = {p.name for p in params}
        first = params[0].name if params else None
        first_arg: Any = chain if (first is None or first in _CHAIN_PARAM_NAMES or first not in _ROWS_PARAM_NAMES) else rows

        kwargs: dict[str, Any] = {}
        optional = {
            "underlying_symbol": chain.underlying_symbol,
            "symbol": chain.underlying_symbol,
            "as_of": chain.as_of,
            "snapshot_timestamp": chain.as_of,
            "provider_name": chain.provider_name,
            "feed_tier": chain.feed_tier,
        }
        for k, v in optional.items():
            if k in names and k != first:
                kwargs[k] = v
        result = fn(first_arg, **kwargs)
        if isinstance(result, bool):
            return len(rows) if result else 0
        if isinstance(result, int):
            return result
        if isinstance(result, (list, tuple)):
            return len(result)
        return len(rows)

    def record_chain(self, chain: OptionChainResponse) -> int:
        """Persist one chain. Returns number of snapshot rows saved (0 if skipped/failed)."""
        if chain.feed_tier == DataFeedTier.FIXTURE and not self.record_fixture:
            self.stats["skipped"] += 1
            return 0
        if chain.feed_tier == DataFeedTier.UNAVAILABLE or (self.skip_empty and not chain.contracts):
            self.stats["skipped"] += 1
            return 0
        key = chain.underlying_symbol
        now = self._clock()
        with self._lock:
            last = self._last_saved.get(key)
            if last is not None and self.min_interval_seconds > 0 and (now - last) < self.min_interval_seconds:
                self.stats["skipped"] += 1
                return 0
            self._last_saved[key] = now
        try:
            n = self._invoke(chain)
        except Exception as exc:
            with self._lock:
                self._last_saved.pop(key, None)
            self.stats["errors"] += 1
            self.last_error = f"{type(exc).__name__}: {str(exc)[:200]}"
            logger.warning("options snapshot persist failed for %s: %s", key, self.last_error)
            if self.raise_on_error:
                raise
            return 0
        self.stats["recorded_chains"] += 1
        self.stats["recorded_rows"] += n
        return n

    # alias
    record = record_chain

    def fetch_and_record(self, provider: Any, symbol: str, **chain_kwargs: Any) -> tuple[OptionChainResponse, int]:
        """Convenience: ``provider.get_option_chain(...)`` then persist."""
        chain = provider.get_option_chain(symbol, **chain_kwargs)
        return chain, self.record_chain(chain)
