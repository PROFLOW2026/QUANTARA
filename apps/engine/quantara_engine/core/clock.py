"""Clock abstractions for real-time and backtest modes."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timezone


class Clock(ABC):
    @abstractmethod
    def now(self) -> datetime:
        ...

    @abstractmethod
    def current_candle_time(self) -> datetime | None:
        ...


class RealClock(Clock):
    def now(self) -> datetime:
        return datetime.now(timezone.utc)

    def current_candle_time(self) -> datetime | None:
        return None


class BacktestClock(Clock):
    def __init__(self) -> None:
        self._current_candle_time: datetime | None = None

    def set_candle_time(self, timestamp: datetime) -> None:
        self._current_candle_time = timestamp

    def now(self) -> datetime:
        if self._current_candle_time is not None:
            return self._current_candle_time
        return datetime.now(timezone.utc)

    def current_candle_time(self) -> datetime | None:
        return self._current_candle_time
