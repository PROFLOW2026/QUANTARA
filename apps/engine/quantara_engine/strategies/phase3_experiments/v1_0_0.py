"""Phase 3 experiments — thin variants on Robots C/D/E logic with distinct slugs."""

from __future__ import annotations

from quantara_engine.live_sim.phase3_constants import (
    EXPERIMENT_ID_BY_SLUG,
    PHASE3_MEAN_REVERSION_EDGE_SLUG,
    PHASE3_MOMENTUM_INTRADAY_SLUG,
    PHASE3_VOLUME_BREAKOUT_SLUG,
)
from quantara_engine.strategies.mean_reversion.v1_0_0 import MeanReversionV1
from quantara_engine.strategies.momentum_continuation.v1_0_0 import MomentumContinuationV1
from quantara_engine.strategies.volatility_squeeze.v1_0_0 import VolatilitySqueezeV1


def _tag_params(slug: str, base: dict) -> dict:
    out = dict(base)
    out["phase3_experiment_id"] = EXPERIMENT_ID_BY_SLUG[slug]
    return out


class Phase3MomentumIntradayV1(MomentumContinuationV1):
    """5m liquid-equity momentum with volume-style extension gate (Robot E variant)."""

    @classmethod
    def strategy_id(cls) -> str:
        return PHASE3_MOMENTUM_INTRADAY_SLUG

    @classmethod
    def version(cls) -> str:
        return "1.0.0"

    @classmethod
    def name(cls) -> str:
        return "Phase3 Momentum Intraday"

    @classmethod
    def description(cls) -> str:
        return (
            "Phase 3 experiment: intraday momentum on 5m using momentum-continuation "
            "logic with tighter extension cap for liquid US names."
        )

    @classmethod
    def supported_timeframes(cls) -> list[str]:
        return ["5m"]

    @classmethod
    def default_parameters(cls) -> dict:
        return _tag_params(
            PHASE3_MOMENTUM_INTRADAY_SLUG,
            {
                **MomentumContinuationV1.default_parameters(),
                "momentum_min_atr": 0.85,
                "max_extension_atr": 1.2,
                "adx_min": 22.0,
                "max_holding_bars": 24,
            },
        )


class Phase3VolumeBreakoutV1(VolatilitySqueezeV1):
    """Range compression → volume-confirmed breakout on 5m (Robot D variant, not ORB)."""

    @classmethod
    def strategy_id(cls) -> str:
        return PHASE3_VOLUME_BREAKOUT_SLUG

    @classmethod
    def version(cls) -> str:
        return "1.0.0"

    @classmethod
    def name(cls) -> str:
        return "Phase3 Volume Breakout"

    @classmethod
    def description(cls) -> str:
        return (
            "Phase 3 experiment: Bollinger/Keltner squeeze breakout on 5m with "
            "slightly lower compression threshold for more events."
        )

    @classmethod
    def supported_timeframes(cls) -> list[str]:
        return ["5m"]

    @classmethod
    def default_parameters(cls) -> dict:
        return _tag_params(
            PHASE3_VOLUME_BREAKOUT_SLUG,
            {
                **VolatilitySqueezeV1.default_parameters(),
                "bb_width_percentile_max": 25.0,
                "compression_min_bars": 4,
                "expansion_atr_mult": 1.0,
                "max_holding_bars": 20,
            },
        )


class Phase3MeanReversionEdgeV1(MeanReversionV1):
    """Stretch-from-mean reversion tuned for 5m dynamic-universe names (Robot C variant)."""

    @classmethod
    def strategy_id(cls) -> str:
        return PHASE3_MEAN_REVERSION_EDGE_SLUG

    @classmethod
    def version(cls) -> str:
        return "1.0.0"

    @classmethod
    def name(cls) -> str:
        return "Phase3 Mean Reversion Edge"

    @classmethod
    def description(cls) -> str:
        return (
            "Phase 3 experiment: 5m mean reversion with slightly wider stretch band "
            "to test edge on rotated scan names."
        )

    @classmethod
    def supported_timeframes(cls) -> list[str]:
        return ["5m"]

    @classmethod
    def default_parameters(cls) -> dict:
        return _tag_params(
            PHASE3_MEAN_REVERSION_EDGE_SLUG,
            {
                **MeanReversionV1.default_parameters(),
                "min_stretch_atr": 1.1,
                "adx_max": 24.0,
                "max_holding_bars": 16,
            },
        )
