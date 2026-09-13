"""Account-specific broker capability checks — pre-intent gate (non-authoritative preview)."""

from __future__ import annotations

from dataclasses import dataclass

from quantara_engine.broker.accounts import RESEARCH_PAPER_ACCOUNT_SLUG
from quantara_engine.broker.execution_model import (
    ExecutionModelVersion,
    resolve_execution_model,
    resolve_execution_model_for_paper_run,
)
from quantara_engine.broker.execution_product import route_execution_product
from quantara_engine.broker.profile import BrokerProfile, profile_for_account_slug
from quantara_engine.broker.types import BrokerRejectionReason
from quantara_engine.competition.leverage import is_paper_competition_portfolio
from quantara_engine.domain.types import Direction, Instrument
from quantara_engine.persistence.store import TradingStore


@dataclass(frozen=True)
class CapabilityCheckResult:
    allowed: bool
    reason: str | None = None
    account_slug: str | None = None
    execution_product: str | None = None


def account_slug_for_portfolio(portfolio_id: str) -> str | None:
    if is_paper_competition_portfolio(portfolio_id):
        return RESEARCH_PAPER_ACCOUNT_SLUG
    return None


def _resolve_model(
    store: TradingStore | None,
    account_slug: str | None,
) -> ExecutionModelVersion:
    if store and account_slug:
        return resolve_execution_model(store, account_slug)
    if store:
        return resolve_execution_model_for_paper_run(store)
    from quantara_engine.broker.execution_model import ACTIVE_EXECUTION_MODEL

    return ACTIVE_EXECUTION_MODEL


def entry_direction_allowed(
    profile: BrokerProfile,
    asset_class: str,
    direction: str,
    *,
    is_close: bool = False,
    symbol: str | None = None,
    execution_model: ExecutionModelVersion | None = None,
) -> CapabilityCheckResult:
    if is_close:
        return CapabilityCheckResult(allowed=True)
    dir_norm = direction.value if isinstance(direction, Direction) else str(direction).lower()
    model = execution_model
    if symbol and model is not None:
        route = route_execution_product(symbol, dir_norm, execution_model=model, is_close=is_close)
        if dir_norm == "short" and not route.short_capable:
            return CapabilityCheckResult(
                allowed=False,
                reason=BrokerRejectionReason.SHORT_NOT_ALLOWED.value,
                execution_product=route.product.value,
            )
        return CapabilityCheckResult(allowed=True, execution_product=route.product.value)

    try:
        rules = profile.rules_for(asset_class)
    except KeyError:
        return CapabilityCheckResult(allowed=False, reason="unsupported_asset")
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
    store: TradingStore | None = None,
) -> CapabilityCheckResult:
    slug = account_slug_for_portfolio(portfolio_id)
    if not slug:
        return CapabilityCheckResult(allowed=True)
    model = _resolve_model(store, slug)
    profile = profile_for_account_slug(slug)
    result = entry_direction_allowed(
        profile,
        str(instrument.asset_class),
        direction,
        is_close=is_close,
        symbol=instrument.symbol,
        execution_model=model,
    )
    return CapabilityCheckResult(
        allowed=result.allowed,
        reason=result.reason,
        account_slug=slug,
        execution_product=result.execution_product,
    )


def check_entry_capability_for_account(
    account_slug: str,
    instrument: Instrument,
    direction: str,
    *,
    is_close: bool = False,
    store: TradingStore | None = None,
) -> CapabilityCheckResult:
    model = _resolve_model(store, account_slug)
    profile = profile_for_account_slug(account_slug)
    result = entry_direction_allowed(
        profile,
        str(instrument.asset_class),
        direction,
        is_close=is_close,
        symbol=instrument.symbol,
        execution_model=model,
    )
    return CapabilityCheckResult(
        allowed=result.allowed,
        reason=result.reason,
        account_slug=account_slug,
        execution_product=result.execution_product,
    )
