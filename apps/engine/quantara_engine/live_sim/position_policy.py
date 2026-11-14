"""Live Sim netting / robot-conflict policy.

Policy ("one position per asset, owner-attributed closes"):

* The owner portfolio holds at most ONE open Live Sim position per symbol, regardless of
  which robot opened it. The robot that opened a position is its only owner.
* A new entry on a symbol that already has an open position (or an accepted entry still
  waiting for its fill) is rejected and logged — never netted. Same direction ->
  ``SYMBOL_POSITION_OPEN``; opposite direction -> ``NETTING_CONFLICT_OPPOSITE_SIGNAL``.
* Therefore a broker-level close can only come from the owning position's SL / TP /
  strategy exit (or a deliberate flatten). A different robot's signal can never silently
  close or reduce another robot's position (the "unexplained close" problem on netting
  accounts).
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import text


@dataclass(frozen=True)
class SymbolPositionVerdict:
    allowed: bool
    reason: str | None = None
    owner_strategy_slug: str | None = None
    owner_direction: str | None = None


def _norm(symbol: str) -> str:
    return symbol.upper().replace("/", "")


def evaluate_symbol_position_policy(
    store,
    *,
    symbol: str,
    direction: str,
    canonical_key: str | None = None,
) -> SymbolPositionVerdict:
    sym = _norm(symbol)
    row = store.session.execute(
        text(
            """
            SELECT p.strategy_slug, p.direction
            FROM live_sim_positions p
            JOIN instruments i ON i.id = p.instrument_id
            JOIN broker_accounts ba ON ba.id = p.broker_account_id
            WHERE p.status = 'open'
              AND ba.slug LIKE 'live-sim-%'
              AND upper(replace(i.symbol, '/', '')) = :sym
            ORDER BY p.opened_at ASC
            LIMIT 1
            """
        ),
        {"sym": sym},
    ).mappings().first()
    if row:
        owner_dir = str(row["direction"]).lower()
        reason = (
            "SYMBOL_POSITION_OPEN"
            if owner_dir == direction.lower()
            else "NETTING_CONFLICT_OPPOSITE_SIGNAL"
        )
        return SymbolPositionVerdict(
            allowed=False,
            reason=reason,
            owner_strategy_slug=str(row["strategy_slug"]),
            owner_direction=owner_dir,
        )

    pending = store.session.execute(
        text(
            """
            SELECT l.strategy_slug, l.direction
            FROM live_sim_allocation_log l
            JOIN broker_accounts ba ON ba.id = l.broker_account_id
            WHERE ba.slug LIKE 'live-sim-%'
              AND upper(replace(l.symbol, '/', '')) = :sym
              AND l.accepted = TRUE
              AND l.broker_order_id IS NULL
              AND l.live_sim_position_id IS NULL
              AND COALESCE(l.metadata->>'pending_execution', 'false') = 'true'
              AND COALESCE(l.metadata->>'expired', 'false') = 'false'
              AND (CAST(:ckey AS text) IS NULL OR l.canonical_opportunity_key <> CAST(:ckey AS text))
            LIMIT 1
            """
        ),
        {"sym": sym, "ckey": canonical_key},
    ).mappings().first()
    if pending:
        owner_dir = str(pending["direction"]).lower()
        reason = (
            "SYMBOL_POSITION_OPEN"
            if owner_dir == direction.lower()
            else "NETTING_CONFLICT_OPPOSITE_SIGNAL"
        )
        return SymbolPositionVerdict(
            allowed=False,
            reason=reason,
            owner_strategy_slug=str(pending["strategy_slug"]),
            owner_direction=owner_dir,
        )
    return SymbolPositionVerdict(allowed=True)
