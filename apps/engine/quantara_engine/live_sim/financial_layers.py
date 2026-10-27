"""Live Sim financial truth: owner aggregate vs broker accounts vs asset envelopes."""

from __future__ import annotations

from decimal import Decimal

_DEFAULT_TOLERANCE = Decimal("0.01")


def live_sim_owner_broker_asset_aligned(
    *,
    owner_equity: Decimal,
    owner_realized: Decimal,
    owner_unrealized: Decimal,
    broker_equity: Decimal,
    broker_realized: Decimal,
    broker_unrealized: Decimal,
    asset_equity: Decimal,
    asset_realized: Decimal,
    asset_unrealized: Decimal,
    tolerance: Decimal = _DEFAULT_TOLERANCE,
) -> bool:
    """True when the three reporting layers match within tolerance.

    Closed-position attribution ledger sums are analytics-only: partial closes on
    still-open positions move broker realized PnL without a matching closed-position
    ledger row — do not compare that ledger sum to owner_realized for this gate.
    """
    diffs = (
        broker_equity - owner_equity,
        broker_realized - owner_realized,
        broker_unrealized - owner_unrealized,
        asset_equity - owner_equity,
        asset_realized - owner_realized,
        asset_unrealized - owner_unrealized,
    )
    return all(abs(d) <= tolerance for d in diffs)
