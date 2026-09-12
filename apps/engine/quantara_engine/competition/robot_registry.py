"""Canonical robot labels and Hebrew display names."""

from __future__ import annotations

ROBOT_LABELS: dict[str, str] = {
    "gold-trend-pullback": "Robot A",
    "opening-range-breakout": "Robot B",
    "mean-reversion": "Robot C",
    "volatility-squeeze": "Robot D",
    "momentum-continuation": "Robot E",
}

ROBOT_LABELS_HE: dict[str, str] = {
    "gold-trend-pullback": "רובוט A — Trend Pullback",
    "opening-range-breakout": "רובוט B — Opening Range Breakout",
    "mean-reversion": "רובוט C — חזרה לממוצע",
    "volatility-squeeze": "רובוט D — התכווצות והתפרצות",
    "momentum-continuation": "רובוט E — מומנטום",
}

MULTI_STRATEGY_SLUGS: frozenset[str] = frozenset(
    {
        "mean-reversion",
        "volatility-squeeze",
        "momentum-continuation",
    }
)


def robot_label_for_slug(strategy_slug: str) -> str:
    return ROBOT_LABELS.get(strategy_slug, strategy_slug)


def is_multi_strategy_slug(strategy_slug: str) -> bool:
    return strategy_slug in MULTI_STRATEGY_SLUGS
