"""Spot crypto cash semantics for broker accounts.

Accounting model (not double-counted):
- ``cash`` — unallocated account cash (residual after spot crypto allocation).
- ``spot_crypto_cash`` — buying power reserved for spot crypto LONG entries.
- ``equity`` — balance + unrealized PnL (from ``build_account_snapshot``).
- ``available_margin`` / ``buying_power`` — margin-based buying power for margined assets.

For spot crypto (100% initial margin), LONG entries consume ``spot_crypto_cash``.
On a fill both ``cash`` and ``spot_crypto_cash`` decrease by notional + fees.

``spot_crypto_cash`` is NOT a separate funded wallet — it mirrors the portion of
``cash`` eligible for spot crypto. On a pristine Live Sim account both must equal
``starting_cash``. Migration 0009 incorrectly seeded ``spot_crypto_cash = 0``.
"""

from __future__ import annotations

from decimal import Decimal


def effective_spot_crypto_cash(*, cash: Decimal, spot_crypto_cash: Decimal) -> Decimal:
    """
    Resolve spot crypto buying power for pre-trade checks.

    When ``spot_crypto_cash`` is zero but ``cash`` is positive, treat as an
    uninitialized mirror (pristine Live Sim seed bug) and use ``cash``.
    """
    if spot_crypto_cash > 0:
        return spot_crypto_cash
    if cash > 0 and spot_crypto_cash == 0:
        return cash
    return spot_crypto_cash


def initial_spot_crypto_cash_for_account(*, starting_cash: Decimal) -> Decimal:
    """Canonical initial spot crypto buying power for a new Live Sim account."""
    return starting_cash
