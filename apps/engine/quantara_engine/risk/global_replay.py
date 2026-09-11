"""Chronological counterfactual replay for global asset open-risk guards."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Callable

from quantara_engine.domain.types import Direction, Instrument, Portfolio, PortfolioStatus, Position, StrategyInstance
from quantara_engine.risk.open_risk_guard import (
    OpenRiskLimits,
    evaluate_global_asset_open_risk,
    evaluate_open_risk_guard,
    sum_open_risk_usd,
)


@dataclass
class ReplayEntry:
    position_id: str
    portfolio_id: str
    strategy_instance_id: str
    direction: str
    opened_at: datetime
    closed_at: datetime | None
    risk_usd: Decimal
    opportunity_idem: str | None = None


@dataclass
class ReplayStats:
    max_simultaneous_positions: int = 0
    peak_open_risk_usd: Decimal = Decimal("0")
    entries_allowed: int = 0
    entries_denied: int = 0
    opportunity_denials: int = 0
    portfolio_denials: int = 0
    strategy_denials: int = 0
    global_denials: int = 0
    first_global_denial: dict | None = field(default=None)


def _normalize_ts(ts: datetime) -> datetime:
    from datetime import timezone

    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts


def row_to_position(row: ReplayEntry, instrument_id: str) -> Position:
    from quantara_engine.domain.types import PositionStatus

    return Position(
        id=row.position_id,
        portfolio_id=row.portfolio_id,
        strategy_instance_id=row.strategy_instance_id,
        instrument_id=instrument_id,
        direction=Direction.LONG if row.direction == "long" else Direction.SHORT,
        quantity=Decimal("1000"),
        entry_price=Decimal("200"),
        current_price=Decimal("200"),
        stop_loss=Decimal("198"),
        take_profit=Decimal("204"),
        actual_risk_amount=row.risk_usd,
        status=PositionStatus.OPEN,
    )


def chronological_global_replay(
    *,
    entries: list[ReplayEntry],
    instrument: Instrument,
    asset_allocated_equity_usd: Decimal,
    limits: OpenRiskLimits,
    portfolio_equity: Decimal = Decimal("2000"),
    capture_first_global_denial: bool = False,
) -> ReplayStats:
    """
    Event-driven counterfactual replay.

    Closes remove positions from simulated state before opens at the same timestamp.
    Denied entries never enter simulated state; peaks reflect guarded state only.
    """
    stats = ReplayStats()
    simulated: dict[str, ReplayEntry] = {}
    consumed_opp: set[str] = set()

    events: list[tuple[datetime, int, str, ReplayEntry]] = []
    for row in entries:
        opened = _normalize_ts(row.opened_at)
        events.append((opened, 1, "open", row))
        if row.closed_at is not None:
            events.append((_normalize_ts(row.closed_at), 0, "close", row))
    events.sort(key=lambda e: (e[0], e[1], e[3].position_id))

    def simulated_positions() -> list[Position]:
        return [row_to_position(r, instrument.id) for r in simulated.values()]

    def portfolio_positions(pid: str) -> list[Position]:
        return [
            row_to_position(r, instrument.id)
            for r in simulated.values()
            if r.portfolio_id == pid
        ]

    def record_peak() -> None:
        risk = sum_open_risk_usd(simulated_positions())
        stats.max_simultaneous_positions = max(stats.max_simultaneous_positions, len(simulated))
        stats.peak_open_risk_usd = max(stats.peak_open_risk_usd, risk)

    for _ts, _prio, kind, row in events:
        if kind == "close":
            simulated.pop(row.position_id, None)
            continue

        if row.opportunity_idem and row.opportunity_idem in consumed_opp:
            stats.opportunity_denials += 1
            stats.entries_denied += 1
            continue

        portfolio = Portfolio(
            id=row.portfolio_id,
            name="replay",
            mode="paper",
            initial_capital=portfolio_equity,
            balance=portfolio_equity,
            equity=portfolio_equity,
            status=PortfolioStatus.ACTIVE,
            peak_equity=portfolio_equity,
        )
        si = StrategyInstance(
            id=row.strategy_instance_id,
            portfolio_id=row.portfolio_id,
            strategy_version_id="sv",
            strategy_slug="replay",
            strategy_version="1.0.0",
            instrument_id=instrument.id,
            timeframe="5m",
            risk_profile_id="rp",
        )

        ok, reason = evaluate_open_risk_guard(
            portfolio=portfolio,
            open_positions=portfolio_positions(row.portfolio_id),
            instrument=instrument,
            strategy_instance=si,
            incremental_risk_usd=row.risk_usd,
            limits=limits,
        )
        if not ok:
            stats.entries_denied += 1
            if reason and "STRATEGY" in reason:
                stats.strategy_denials += 1
            else:
                stats.portfolio_denials += 1
            continue

        current_global = sum_open_risk_usd(simulated_positions())
        ok, reason = evaluate_global_asset_open_risk(
            global_open_positions_on_asset=simulated_positions(),
            incremental_risk_usd=row.risk_usd,
            asset_allocated_equity_usd=asset_allocated_equity_usd,
            limits=limits,
        )
        if not ok:
            stats.global_denials += 1
            stats.entries_denied += 1
            if capture_first_global_denial and stats.first_global_denial is None:
                limit_usd = (
                    asset_allocated_equity_usd
                    * limits.max_global_asset_open_risk_pct
                    / Decimal("100")
                ).quantize(Decimal("0.01"))
                stats.first_global_denial = {
                    "position_id": row.position_id,
                    "current_risk_usd": float(current_global),
                    "candidate_risk_usd": float(row.risk_usd),
                    "prospective_risk_usd": float(current_global + row.risk_usd),
                    "asset_allocated_equity_usd": float(asset_allocated_equity_usd),
                    "limit_pct": float(limits.max_global_asset_open_risk_pct or 0),
                    "limit_usd": float(limit_usd),
                    "decision": "DENY",
                    "reason": reason,
                }
            continue

        simulated[row.position_id] = row
        if row.opportunity_idem:
            consumed_opp.add(row.opportunity_idem)
        stats.entries_allowed += 1
        record_peak()

    stats.peak_open_risk_usd = stats.peak_open_risk_usd.quantize(Decimal("0.01"))
    return stats
