"""Initial QUANTARA Options Lab accounts.

Three AI-mode headline accounts ($5k / $10k / $25k) plus five isolated Rules-mode
cohorts on the same $10,000 capital, so each strategy family can be compared
fairly against the all-strategy AI account (`options-10k`).

Rule profiles are per-account configuration (never global simulation blockers):
the sim_flexible profile keeps margin on, approval level 3 and PDT enforcement off.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Optional

from quantara_options.contracts import AccountRuleProfile, DecisionMode, StrategyType

SCOPE_LONG_ONLY = "long_only"
SCOPE_CREDIT_SPREADS = "credit_spreads"
SCOPE_WHEEL = "wheel_csp_cc"
SCOPE_IRON_CONDOR = "iron_condor"

# strategy_scope -> allowed strategy_type values. None scope = every strategy.
STRATEGY_SCOPE_TYPES: dict[str, frozenset[str]] = {
    SCOPE_LONG_ONLY: frozenset({StrategyType.LONG_CALL.value, StrategyType.LONG_PUT.value}),
    SCOPE_CREDIT_SPREADS: frozenset(
        {
            StrategyType.BULL_PUT_CREDIT_SPREAD.value,
            StrategyType.BEAR_CALL_CREDIT_SPREAD.value,
        }
    ),
    SCOPE_WHEEL: frozenset(
        {
            StrategyType.CASH_SECURED_PUT.value,
            StrategyType.COVERED_CALL.value,
            StrategyType.WHEEL.value,
        }
    ),
    SCOPE_IRON_CONDOR: frozenset({StrategyType.IRON_CONDOR.value}),
}


def strategy_types_for_scope(scope: Optional[str]) -> Optional[frozenset[str]]:
    """Return allowed strategy types for a scope, or None when unrestricted."""
    if not scope:
        return None
    return STRATEGY_SCOPE_TYPES.get(scope)


@dataclass(frozen=True)
class LabAccountSeed:
    slug: str
    name: str
    starting_capital: Decimal
    mode: DecisionMode
    strategy_scope: Optional[str]
    experiment_group: str
    rule_profile: AccountRuleProfile

    def rule_profile_json(self) -> dict[str, Any]:
        return self.rule_profile.model_dump(mode="json")


def _profile(**overrides: Any) -> AccountRuleProfile:
    base: dict[str, Any] = {
        "profile_name": "sim_flexible",
        "options_approval_level": 3,
        "margin_enabled": True,
        "enforce_pdt_rule": False,
    }
    base.update(overrides)
    return AccountRuleProfile(**base)


_CAPITAL_10K = Decimal("10000.00")
_COHORT_GROUP = "options_lab_cohort_10k"


def _cohort(slug: str, name: str, scope: Optional[str]) -> LabAccountSeed:
    return LabAccountSeed(
        slug=slug,
        name=name,
        starting_capital=_CAPITAL_10K,
        mode=DecisionMode.RULES,
        strategy_scope=scope,
        experiment_group=_COHORT_GROUP,
        rule_profile=_profile(),
    )


LAB_ACCOUNT_SEEDS: tuple[LabAccountSeed, ...] = (
    LabAccountSeed(
        slug="options-5k",
        name="Options $5K (AI)",
        starting_capital=Decimal("5000.00"),
        mode=DecisionMode.AI,
        strategy_scope=None,
        experiment_group="options_lab",
        rule_profile=_profile(
            options_approval_level=3,
            margin_enabled=True,
            enforce_pdt_rule=False,
        ),
    ),
    LabAccountSeed(
        slug="options-10k",
        name="Options $10K (AI)",
        starting_capital=_CAPITAL_10K,
        mode=DecisionMode.AI,
        strategy_scope=None,
        experiment_group="options_lab",
        rule_profile=_profile(),
    ),
    LabAccountSeed(
        slug="options-25k",
        name="Options $25K (AI)",
        starting_capital=Decimal("25000.00"),
        mode=DecisionMode.AI,
        strategy_scope=None,
        experiment_group="options_lab",
        rule_profile=_profile(pdt_equity_threshold_usd=Decimal("25000")),
    ),
    _cohort("options-10k-rules", "Options $10K (Rules, all strategies)", None),
    _cohort("options-10k-long-only", "Options $10K (Rules, long only)", SCOPE_LONG_ONLY),
    _cohort(
        "options-10k-credit-spreads",
        "Options $10K (Rules, credit spreads)",
        SCOPE_CREDIT_SPREADS,
    ),
    _cohort("options-10k-wheel", "Options $10K (Rules, wheel CSP/CC)", SCOPE_WHEEL),
    _cohort("options-10k-iron-condor", "Options $10K (Rules, iron condor)", SCOPE_IRON_CONDOR),
)

LAB_ACCOUNT_SLUGS: tuple[str, ...] = tuple(s.slug for s in LAB_ACCOUNT_SEEDS)
