"""Minimal runtime cache — worker heartbeats only (not financial data)."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class RuntimeCache:
    """In-process worker heartbeat overlay; DB remains source of truth."""

    worker_status: dict[str, dict] = field(default_factory=dict)

    def ensure_defaults(self) -> None:
        if not self.worker_status:
            self.worker_status = {
                "data_fetcher": {"status": "idle", "last_run": None},
                "strategy_runner": {"status": "idle", "last_run": None},
                "backtest_runner": {"status": "idle", "last_run": None},
            }


runtime_cache = RuntimeCache()
runtime_cache.ensure_defaults()
