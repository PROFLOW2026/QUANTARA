"""Execution account registry — research paper broker + live simulation."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

# Research paper broker (existing $320K competition account).
RESEARCH_PAPER_ACCOUNT_SLUG = "quantara_paper_competition"

# Live simulation of future real account.
LIVE_SIM_10K_ACCOUNT_SLUG = "live-sim-10k"

# Fixed virtual portfolio id for live-sim broker attribution (not a research portfolio).
LIVE_SIM_VIRTUAL_PORTFOLIO_ID = "00000000-0000-4000-8000-000000000001"

# Backward-compatible alias used across research broker code paths.
PAPER_ACCOUNT_SLUG = RESEARCH_PAPER_ACCOUNT_SLUG


@dataclass(frozen=True)
class ExecutionAccountSpec:
    slug: str
    label_he: str
    starting_cash: Decimal
    profile_slug: str
    concentration_mode: str  # OBSERVE | ENFORCE


RESEARCH_PAPER_ACCOUNT = ExecutionAccountSpec(
    slug=RESEARCH_PAPER_ACCOUNT_SLUG,
    label_he="חשבון המחקר",
    starting_cash=Decimal("320000"),
    profile_slug="quantara_standard_paper",
    concentration_mode="OBSERVE",
)

LIVE_SIM_10K_ACCOUNT = ExecutionAccountSpec(
    slug=LIVE_SIM_10K_ACCOUNT_SLUG,
    label_he="סימולציית $10,000",
    starting_cash=Decimal("10000"),
    profile_slug="quantara_live_sim_10k",
    concentration_mode="ENFORCE",
)

EXECUTION_ACCOUNTS: dict[str, ExecutionAccountSpec] = {
    RESEARCH_PAPER_ACCOUNT.slug: RESEARCH_PAPER_ACCOUNT,
    LIVE_SIM_10K_ACCOUNT.slug: LIVE_SIM_10K_ACCOUNT,
}


def get_execution_account(slug: str) -> ExecutionAccountSpec | None:
    return EXECUTION_ACCOUNTS.get(slug)


def is_live_sim_account(slug: str) -> bool:
    return slug == LIVE_SIM_10K_ACCOUNT_SLUG


def is_research_paper_account(slug: str) -> bool:
    return slug == RESEARCH_PAPER_ACCOUNT_SLUG
