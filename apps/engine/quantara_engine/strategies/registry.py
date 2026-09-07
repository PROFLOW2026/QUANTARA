"""Strategy registry."""

from __future__ import annotations

from typing import Type

from quantara_engine.strategies.base import BaseStrategy
from quantara_engine.strategies.gold_trend_pullback.v1_0_0 import GoldTrendPullbackV1

STRATEGY_REGISTRY: dict[str, dict[str, Type[BaseStrategy]]] = {
    "gold-trend-pullback": {
        "1.0.0": GoldTrendPullbackV1,
    },
}


def register(strategy_class: Type[BaseStrategy]) -> Type[BaseStrategy]:
    slug = strategy_class.strategy_id()
    version = strategy_class.version()
    STRATEGY_REGISTRY.setdefault(slug, {})[version] = strategy_class
    return strategy_class


def get(slug: str, version: str) -> Type[BaseStrategy]:
    try:
        return STRATEGY_REGISTRY[slug][version]
    except KeyError as exc:
        raise KeyError(f"Strategy {slug}@{version} not found") from exc


def get_latest(slug: str) -> Type[BaseStrategy]:
    versions = STRATEGY_REGISTRY.get(slug, {})
    if not versions:
        raise KeyError(f"Strategy {slug} not found")
    latest = sorted(versions.keys(), key=lambda v: tuple(int(x) for x in v.split(".")))[-1]
    return versions[latest]


def list_all() -> list[dict]:
    result = []
    for slug, versions in STRATEGY_REGISTRY.items():
        for version, cls in versions.items():
            result.append(
                {
                    "slug": slug,
                    "version": version,
                    "name": cls.name(),
                    "description": cls.description(),
                    "supported_instruments": cls.supported_instruments(),
                    "supported_timeframes": cls.supported_timeframes(),
                    "default_parameters": cls.default_parameters(),
                    "risk_profile_compatibility": cls.risk_profile_compatibility(),
                }
            )
    return result


def validate_parameters(slug: str, version: str, params: dict) -> dict:
    cls = get(slug, version)
    merged = {**cls.default_parameters(), **params}
    return merged
