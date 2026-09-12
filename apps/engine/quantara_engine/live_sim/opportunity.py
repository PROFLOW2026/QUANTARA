"""Canonical live-sim opportunity identity — ignores research risk tier."""

from __future__ import annotations

import hashlib


def live_sim_canonical_opportunity_key(
    *,
    strategy_slug: str,
    strategy_version: str,
    symbol: str,
    timeframe: str,
    direction: str,
    opportunity_key: str,
) -> str:
    """One live-sim candidate per real opportunity (not per research portfolio/risk tier)."""
    raw = (
        f"live-sim:{strategy_slug}:v{strategy_version}:{symbol}:"
        f"{timeframe}:{direction}:{opportunity_key}"
    )
    return hashlib.sha256(raw.encode()).hexdigest()


def live_sim_execution_idempotency_key(account_slug: str, canonical_key: str) -> str:
    raw = f"{account_slug}:entry:{canonical_key}"
    return hashlib.sha256(raw.encode()).hexdigest()
