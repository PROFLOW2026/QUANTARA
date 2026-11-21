"""QUANTARA Options simulation engine (fills, fees, collateral, accounting, lifecycle)."""

from quantara_options.sim.accounting import (
    AccountTotals,
    SimAccountState,
    SimJournalEvent,
    SimLeg,
    SimStockLot,
    SimStrategy,
    check_conservation,
    compute_account_totals,
    new_account_state,
    to_account_summary,
)
from quantara_options.sim.engine import (
    InMemoryOptionsRepository,
    OptionsSimRepository,
    OptionsSimulationEngine,
    SimOperationResult,
)
from quantara_options.sim.fees_and_slippage import (
    FeeBreakdown,
    FillAction,
    FillUnavailableError,
    RegulatoryFeeSchedule,
    SlippageConfig,
)
from quantara_options.sim.lifecycle import ExitPolicy
from quantara_options.sim.margin_and_collateral import (
    CollateralRequirement,
    StructureError,
    compute_collateral,
)

__all__ = [
    "AccountTotals",
    "CollateralRequirement",
    "ExitPolicy",
    "FeeBreakdown",
    "FillAction",
    "FillUnavailableError",
    "InMemoryOptionsRepository",
    "OptionsSimRepository",
    "OptionsSimulationEngine",
    "RegulatoryFeeSchedule",
    "SimAccountState",
    "SimJournalEvent",
    "SimLeg",
    "SimOperationResult",
    "SimStockLot",
    "SimStrategy",
    "SlippageConfig",
    "StructureError",
    "check_conservation",
    "compute_account_totals",
    "compute_collateral",
    "new_account_state",
    "to_account_summary",
]
