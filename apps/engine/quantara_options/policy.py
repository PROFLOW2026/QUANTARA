"""Options Lab trading-policy constants (v2) — entry/exit/risk calibration.

Versioned so Forward Simulation results after this cut can be compared to
2026-11-23 history without rewriting past fills or equity.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from quantara_options.contracts import StrategyType

# Effective from deploy of this module; stamped on new opens + engine cycles.
POLICY_VERSION = "options_lab_v2_2026-11-24"
POLICY_EFFECTIVE_DATE_NY = "2026-11-24"


@dataclass(frozen=True)
class CreditEntryThresholds:
    """Natural (bid/ask) credit / width floors after costs — tunable per structure."""

    credit_vertical: float = 0.18
    iron_condor: float = 0.18
    cash_secured_put_min_credit_per_share: float = 0.10
    covered_call_min_credit_per_share: float = 0.05
    max_leg_spread_pct: float = 0.12
    max_leg_spread_pct_single_name: float = 0.15
    fee_buffer_per_contract_usd: float = 0.80  # commission+clearing ballpark


@dataclass(frozen=True)
class ExitThresholds:
    """Soft stop = confirmed over N mark cycles; emergency = immediate."""

    # Long single / debit verticals: fraction of debit paid
    long_debit_stop_fraction: float = 0.35
    long_debit_take_profit_fraction: float = 0.60
    # Credit verticals / IC: fraction of defined max_loss (width - credit)
    credit_soft_stop_of_max_loss: float = 0.45
    credit_emergency_of_max_loss: float = 0.75
    credit_soft_confirm_cycles: int = 2
    # CSP / CC / wheel: no credit-multiple auto-stop (assignment path); optional soft mark
    capital_secured_soft_stop_of_max_loss: Optional[float] = None  # None = ride
    capital_secured_emergency_of_max_loss: float = 0.90


ENTRY_THRESHOLDS = CreditEntryThresholds()
EXIT_THRESHOLDS = ExitThresholds()


def min_natural_credit_to_width(strategy_type: StrategyType) -> float:
    if strategy_type == StrategyType.IRON_CONDOR:
        return ENTRY_THRESHOLDS.iron_condor
    if strategy_type in (
        StrategyType.BULL_PUT_CREDIT_SPREAD,
        StrategyType.BEAR_CALL_CREDIT_SPREAD,
    ):
        return ENTRY_THRESHOLDS.credit_vertical
    return ENTRY_THRESHOLDS.credit_vertical


def natural_net_per_share(legs: list[tuple[float, float, str]]) -> float:
    """legs: list of (bid, ask, side) where side is 'short' or 'long'.
    Short sells at bid, long buys at ask — executable natural premium (+credit)."""
    net = 0.0
    for bid, ask, side in legs:
        if side == "short":
            net += float(bid)
        else:
            net -= float(ask)
    return net


def credit_after_fees_per_share(
    natural_credit: float,
    *,
    n_option_legs: int,
    multiplier: int = 100,
    qty: int = 1,
    fee_per_contract: float = ENTRY_THRESHOLDS.fee_buffer_per_contract_usd,
) -> float:
    """Convert gross natural credit/share into net after estimated round-trip open fees."""
    if natural_credit <= 0 or multiplier <= 0 or qty <= 0:
        return natural_credit
    fees_total = fee_per_contract * n_option_legs * qty
    fees_per_share = fees_total / (multiplier * qty)
    return natural_credit - fees_per_share


# Capital-tier risk profiles (applied at seed + risk_guard). Keys are starting capital.
TIERED_RISK_PROFILES: dict[int, dict] = {
    5000: {
        "profile_name": "sim_5k_v2",
        "max_risk_per_trade_pct": Decimal("3.0"),
        "max_daily_loss_pct": Decimal("4.0"),
        "max_collateral_pct": Decimal("50.0"),
        "max_open_strategies": 4,
        "max_drawdown_pct": Decimal("15.0"),
    },
    10000: {
        "profile_name": "sim_10k_v2",
        "max_risk_per_trade_pct": Decimal("4.0"),
        "max_daily_loss_pct": Decimal("5.0"),
        "max_collateral_pct": Decimal("55.0"),
        "max_open_strategies": 5,
        "max_drawdown_pct": Decimal("18.0"),
    },
    25000: {
        "profile_name": "sim_25k_v2",
        "max_risk_per_trade_pct": Decimal("4.0"),
        "max_daily_loss_pct": Decimal("5.0"),
        "max_collateral_pct": Decimal("60.0"),
        "max_open_strategies": 6,
        "max_drawdown_pct": Decimal("20.0"),
    },
}


def tier_profile_for_capital(starting_capital: float | Decimal) -> dict:
    cap = float(starting_capital)
    if cap <= 7500:
        return dict(TIERED_RISK_PROFILES[5000])
    if cap <= 15000:
        return dict(TIERED_RISK_PROFILES[10000])
    return dict(TIERED_RISK_PROFILES[25000])
