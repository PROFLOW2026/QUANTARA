"""Simulated broker latency as lifecycle timestamps — no blocking sleep."""

from __future__ import annotations

from datetime import datetime, timedelta

from quantara_engine.broker.execution_product import EXECUTION_SIM_DEFAULTS


def simulated_latency_ms() -> int:
    return int(EXECUTION_SIM_DEFAULTS.get("latency_ms_market", 150))


def lifecycle_timestamps(submitted_at: datetime) -> tuple[datetime, datetime]:
    """Return (accepted_at, first_fill_at) with deterministic simulated latency."""
    ms = simulated_latency_ms()
    accepted_at = submitted_at + timedelta(milliseconds=max(1, ms // 2))
    first_fill_at = submitted_at + timedelta(milliseconds=ms)
    return accepted_at, first_fill_at


def fill_timestamp_for_sequence(submitted_at: datetime, sequence: int) -> datetime:
    """Stagger multi-fill timestamps without blocking."""
    _, first = lifecycle_timestamps(submitted_at)
    return first + timedelta(milliseconds=10 * max(0, sequence - 1))
