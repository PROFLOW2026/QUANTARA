"""Canonical trading opportunity identity and consumption rules."""

from __future__ import annotations

import hashlib
from datetime import datetime
from decimal import Decimal

from quantara_engine.domain.types import Signal, SignalAction


def orb_opportunity_key(
    *,
    symbol: str,
    session_date: str,
    direction: str,
    range_high: Decimal | float,
    range_low: Decimal | float,
    strategy_version: str = "1.0.0",
) -> str:
    return (
        f"orb:{symbol}:{session_date}:{direction}:"
        f"{Decimal(str(range_high)):.5f}:{Decimal(str(range_low)):.5f}:v{strategy_version}"
    )


def pullback_opportunity_key(
    *,
    symbol: str,
    timeframe: str,
    direction: str,
    setup_candle_timestamp: datetime,
) -> str:
    ts = setup_candle_timestamp.replace(second=0, microsecond=0).isoformat()
    return f"pullback:{symbol}:{timeframe}:{direction}:{ts}"


def opportunity_key_from_signal(
    signal: Signal,
    *,
    symbol: str,
    timeframe: str,
    strategy_slug: str,
    setup_candle_timestamp: datetime,
    strategy_version: str = "1.0.0",
) -> str | None:
    if signal.action not in (SignalAction.BUY, SignalAction.SELL):
        return None
    direction = "long" if signal.action == SignalAction.BUY else "short"
    meta = signal.metadata or {}

    if strategy_slug == "opening-range-breakout":
        session_date = meta.get("session_date")
        if not session_date:
            return None
        return orb_opportunity_key(
            symbol=symbol,
            session_date=str(session_date),
            direction=direction,
            range_high=meta.get("opening_range_high", 0),
            range_low=meta.get("opening_range_low", 0),
            strategy_version=strategy_version,
        )

    if strategy_slug == "gold-trend-pullback":
        return pullback_opportunity_key(
            symbol=symbol,
            timeframe=timeframe,
            direction=direction,
            setup_candle_timestamp=setup_candle_timestamp,
        )

    return pullback_opportunity_key(
        symbol=symbol,
        timeframe=timeframe,
        direction=direction,
        setup_candle_timestamp=setup_candle_timestamp,
    )


def opportunity_idempotency_key(strategy_instance_id: str, opportunity_key: str) -> str:
    raw = f"{strategy_instance_id}:{opportunity_key}"
    return hashlib.sha256(raw.encode()).hexdigest()


def legacy_idempotency_key(
    strategy_instance_id: str,
    signal_candle_timestamp: datetime,
    direction: str,
) -> str:
    raw = f"{strategy_instance_id}:{signal_candle_timestamp.isoformat()}:{direction}"
    return hashlib.sha256(raw.encode()).hexdigest()
