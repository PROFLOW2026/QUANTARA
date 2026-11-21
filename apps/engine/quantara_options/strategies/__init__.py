"""QUANTARA Options strategies: catalog, close/roll evaluation and opportunity scanner."""

from quantara_options.strategies.catalog import (
    STRATEGY_CATALOG,
    StrategyActionRecommendation,
    StrategySpec,
    catalog_as_dicts,
    derive_wheel_state,
    evaluate_open_strategy_actions,
    get_strategy_spec,
    next_wheel_state,
    parse_strategy_scope,
    wing_width_for_capital,
)
from quantara_options.strategies.scanner import (
    OptionsOpportunityScanner,
    ScanContext,
    ScannerConfig,
    resize_candidate,
)

__all__ = [
    "STRATEGY_CATALOG",
    "OptionsOpportunityScanner",
    "ScanContext",
    "ScannerConfig",
    "StrategyActionRecommendation",
    "StrategySpec",
    "catalog_as_dicts",
    "derive_wheel_state",
    "evaluate_open_strategy_actions",
    "get_strategy_spec",
    "next_wheel_state",
    "parse_strategy_scope",
    "resize_candidate",
    "wing_width_for_capital",
]
