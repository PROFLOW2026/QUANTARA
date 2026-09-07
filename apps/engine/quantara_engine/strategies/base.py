"""Strategy framework base types."""

from __future__ import annotations

from abc import ABC, abstractmethod

from quantara_engine.domain.types import Signal, StrategyContext


class BaseStrategy(ABC):
    @classmethod
    @abstractmethod
    def strategy_id(cls) -> str:
        ...

    @classmethod
    @abstractmethod
    def version(cls) -> str:
        ...

    @classmethod
    @abstractmethod
    def name(cls) -> str:
        ...

    @classmethod
    @abstractmethod
    def description(cls) -> str:
        ...

    @classmethod
    @abstractmethod
    def supported_instruments(cls) -> list[str]:
        ...

    @classmethod
    @abstractmethod
    def supported_timeframes(cls) -> list[str]:
        ...

    @classmethod
    @abstractmethod
    def default_parameters(cls) -> dict:
        ...

    @classmethod
    @abstractmethod
    def parameters_schema(cls) -> dict:
        ...

    @classmethod
    @abstractmethod
    def risk_profile_compatibility(cls) -> list[str]:
        ...

    @abstractmethod
    def evaluate(self, candles: list, context: StrategyContext) -> Signal:
        ...
