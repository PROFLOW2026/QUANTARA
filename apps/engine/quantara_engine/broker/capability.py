"""Account-specific broker capability checks — pre-intent gate (non-authoritative preview)."""

from __future__ import annotations

from dataclasses import dataclass

from quantara_engine.broker.accounts import RESEARCH_PAPER_ACCOUNT_SLUG
from quantara_engine.broker.profile import BrokerProfile, profile_for_account_slug
from quantara_engine.broker.types import BrokerRejectionReason
from quantara_engine.competition.leverage import is_paper_competition_portfolio
from quantara_engine.domain.types import Direction, Instrument


@dataclass(frozen=True)
class CapabilityCheckResult:
    allowed: bool
    reason: str | None = None
    account_slug: str | None = None


def account_slug_for_portfolio(portfolio_id: str) -> str | None:
    if is_paper_competition_portfolio(portfolio_id):
        return RESEARCH_PAPER_ACCOUNT_SLUG
    return None


def entry_direction_allowed(
    profile: BrokerProfile,
    asset_class: str,
    direction: str,
    *,
    is_close: bool = False,
) -> CapabilityCheckResult:
    if is_close:
        return CapabilityCheckResult(allowed=True)
    dir_norm = direction.value if isinstance(direction, Direction) else str(direction).lower()
    try:
        rules = profile.rules_for(asset_class)
    except KeyError:
        return CapabilityCheckResult(
            allowed=False,
            reason="unsupported_asset",
        )
    if dir_norm == "short" and not rules.shorting_allowed:
        return CapabilityCheckResult(
            allowed=False,
            reason=BrokerRejectionReason.SHORT_NOT_ALLOWED.value,
        )
    return CapabilityCheckResult(allowed=True)


def check_entry_capability_for_portfolio(
    portfolio_id: str,
    instrument: Instrument,
    direction: str,
    *,
    is_close: bool = False,
) -> CapabilityCheckResult:
    slug = account_slug_for_portfolio(portfolio_id)
    if not slug:
        return CapabilityCheckResult(allowed=True)
    profile = profile_for_account_slug(slug)
    result = entry_direction_allowed(
        profile, str(instrument.asset_class), direction, is_close=is_close
    )
    return CapabilityCheckResult(
        allowed=result.allowed,
        reason=result.reason,
        account_slug=slug,
    )


def check_entry_capability_for_account(
    account_slug: str,
    instrument: Instrument,
    direction: str,
    *,
    is_close: bool = False,
) -> CapabilityCheckResult:
    profile = profile_for_account_slug(account_slug)
    result = entry_direction_allowed(
        profile, str(instrument.asset_class), direction, is_close=is_close
    )
    return CapabilityCheckResult(
        allowed=result.allowed,
        reason=result.reason,
        account_slug=account_slug,
    )
