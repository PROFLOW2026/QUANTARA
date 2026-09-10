"""Batched SQL aggregates for competition portfolio dashboards."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import func, select

from quantara_engine.domain.types import Direction, Position
from quantara_engine.models.enums import PositionStatus as OrmPositionStatus
from quantara_engine.models.instruments import Candle as OrmCandle
from quantara_engine.models.instruments import Instrument as OrmInstrument
from quantara_engine.models.portfolio import PortfolioSnapshot as OrmPortfolioSnapshot
from quantara_engine.models.trading import Position as OrmPosition
from quantara_engine.models.trading import Trade as OrmTrade

if TYPE_CHECKING:
    from quantara_engine.persistence.store import TradingStore


def _uuids(ids: list[str]) -> list[uuid.UUID]:
    return [uuid.UUID(i) for i in ids]


@dataclass(frozen=True)
class TradeBatchMetrics:
    closed_trades_count: int = 0
    realized_pnl: Decimal = Decimal("0")
    win_rate: float | None = None


@dataclass(frozen=True)
class AssetBatchMetrics:
    open_positions: int = 0
    closed_trades: int = 0
    realized_pnl: Decimal = Decimal("0")
    unrealized_pnl: Decimal = Decimal("0")


def batch_trade_metrics(store: TradingStore, portfolio_ids: list[str]) -> dict[str, TradeBatchMetrics]:
    if not portfolio_ids:
        return {}
    ids = _uuids(portfolio_ids)
    wins_expr = func.count().filter(OrmTrade.realized_pnl > 0)
    rows = store.session.execute(
        select(
            OrmTrade.portfolio_id,
            func.count().label("closed_count"),
            func.coalesce(func.sum(OrmTrade.realized_pnl), 0).label("realized_pnl"),
            wins_expr.label("wins"),
        )
        .where(
            OrmTrade.portfolio_id.in_(ids),
            OrmTrade.backtest_run_id.is_(None),
        )
        .group_by(OrmTrade.portfolio_id)
    ).all()
    out: dict[str, TradeBatchMetrics] = {}
    for portfolio_id, closed_count, realized_pnl, wins in rows:
        pid = str(portfolio_id)
        total = int(closed_count or 0)
        win_rate = round(float(wins) / total * 100, 1) if total else None
        out[pid] = TradeBatchMetrics(
            closed_trades_count=total,
            realized_pnl=Decimal(str(realized_pnl or 0)),
            win_rate=win_rate,
        )
    return out


def batch_open_positions_by_portfolio(
    store: TradingStore, portfolio_ids: list[str]
) -> dict[str, list[Position]]:
    if not portfolio_ids:
        return {}
    ids = _uuids(portfolio_ids)
    rows = store.session.scalars(
        select(OrmPosition).where(
            OrmPosition.portfolio_id.in_(ids),
            OrmPosition.status == OrmPositionStatus.OPEN,
        )
    ).all()
    positions = [store._position_to_domain(row) for row in rows]
    store._hydrate_position_strategy_versions(positions)
    grouped: dict[str, list[Position]] = {pid: [] for pid in portfolio_ids}
    for pos in positions:
        grouped.setdefault(pos.portfolio_id, []).append(pos)
    return grouped


def batch_asset_metrics(
    store: TradingStore, portfolio_ids: list[str]
) -> dict[str, AssetBatchMetrics]:
    if not portfolio_ids:
        return {}
    ids = _uuids(portfolio_ids)
    open_rows = store.session.execute(
        select(
            OrmPosition.instrument_id,
            func.count().label("open_count"),
            func.coalesce(func.sum(OrmPosition.unrealized_pnl), 0).label("unrealized"),
        )
        .where(
            OrmPosition.portfolio_id.in_(ids),
            OrmPosition.status == OrmPositionStatus.OPEN,
        )
        .group_by(OrmPosition.instrument_id)
    ).all()
    trade_rows = store.session.execute(
        select(
            OrmTrade.instrument_id,
            func.count().label("closed_count"),
            func.coalesce(func.sum(OrmTrade.realized_pnl), 0).label("realized"),
        )
        .where(
            OrmTrade.portfolio_id.in_(ids),
            OrmTrade.backtest_run_id.is_(None),
        )
        .group_by(OrmTrade.instrument_id)
    ).all()
    out: dict[str, AssetBatchMetrics] = {}
    for instrument_id, open_count, unrealized in open_rows:
        iid = str(instrument_id)
        out[iid] = AssetBatchMetrics(
            open_positions=int(open_count or 0),
            unrealized_pnl=Decimal(str(unrealized or 0)),
        )
    for instrument_id, closed_count, realized in trade_rows:
        iid = str(instrument_id)
        existing = out.get(iid, AssetBatchMetrics())
        out[iid] = AssetBatchMetrics(
            open_positions=existing.open_positions,
            closed_trades=int(closed_count or 0),
            realized_pnl=Decimal(str(realized or 0)),
            unrealized_pnl=existing.unrealized_pnl,
        )
    return out


def list_positions_for_portfolios(
    store: TradingStore,
    portfolio_ids: list[str],
    *,
    open_only: bool = True,
    status: str | None = None,
) -> list[Position]:
    if not portfolio_ids:
        return []
    ids = _uuids(portfolio_ids)
    stmt = select(OrmPosition).where(OrmPosition.portfolio_id.in_(ids))
    if status:
        stmt = stmt.where(OrmPosition.status == OrmPositionStatus(status))
    elif open_only:
        stmt = stmt.where(OrmPosition.status == OrmPositionStatus.OPEN)
    rows = store.session.scalars(stmt).all()
    positions = [store._position_to_domain(row) for row in rows]
    store._hydrate_position_strategy_versions(positions)
    return positions


def batch_instruments_by_id(store: TradingStore, instrument_ids: list[str]) -> dict[str, Any]:
    if not instrument_ids:
        return {}
    ids = _uuids(instrument_ids)
    rows = store.session.scalars(select(OrmInstrument).where(OrmInstrument.id.in_(ids))).all()
    return {str(row.id): store._instrument_to_domain(row) for row in rows}


def batch_candle_counts(
    store: TradingStore,
    instrument_ids: list[str],
    timeframes: tuple[str, ...] = ("5m", "15m", "1h"),
) -> dict[str, dict[str, int]]:
    if not instrument_ids:
        return {}
    ids = _uuids(instrument_ids)
    rows = store.session.execute(
        select(
            OrmCandle.instrument_id,
            OrmCandle.timeframe,
            func.count().label("cnt"),
        )
        .where(
            OrmCandle.instrument_id.in_(ids),
            OrmCandle.timeframe.in_(timeframes),
        )
        .group_by(OrmCandle.instrument_id, OrmCandle.timeframe)
    ).all()
    out: dict[str, dict[str, int]] = {
        iid: {tf: 0 for tf in timeframes} for iid in instrument_ids
    }
    for instrument_id, timeframe, cnt in rows:
        iid = str(instrument_id)
        out.setdefault(iid, {tf: 0 for tf in timeframes})[timeframe] = int(cnt or 0)
    return out


def batch_latest_candle_timestamps(
    store: TradingStore,
    instrument_ids: list[str],
    timeframe: str = "5m",
) -> dict[str, Any]:
    if not instrument_ids:
        return {}
    ids = _uuids(instrument_ids)
    rows = store.session.execute(
        select(
            OrmCandle.instrument_id,
            func.max(OrmCandle.timestamp).label("last_ts"),
        )
        .where(
            OrmCandle.instrument_id.in_(ids),
            OrmCandle.timeframe == timeframe,
        )
        .group_by(OrmCandle.instrument_id)
    ).all()
    return {str(instrument_id): last_ts for instrument_id, last_ts in rows}


def batch_latest_candle_closes(
    store: TradingStore,
    instrument_ids: list[str],
    timeframe: str = "5m",
) -> dict[str, Decimal]:
    """Latest close per instrument (window row_number)."""
    if not instrument_ids:
        return {}
    from sqlalchemy import desc

    subq = (
        select(
            OrmCandle.instrument_id,
            OrmCandle.close,
            func.row_number()
            .over(
                partition_by=OrmCandle.instrument_id,
                order_by=desc(OrmCandle.timestamp),
            )
            .label("rn"),
        )
        .where(
            OrmCandle.instrument_id.in_(_uuids(instrument_ids)),
            OrmCandle.timeframe == timeframe,
        )
        .subquery()
    )
    rows = store.session.execute(
        select(subq.c.instrument_id, subq.c.close).where(subq.c.rn == 1)
    ).all()
    return {str(instrument_id): Decimal(str(close)) for instrument_id, close in rows}


def batch_recent_snapshots(
    store: TradingStore,
    portfolio_ids: list[str],
    *,
    limit_per_portfolio: int = 100,
) -> dict[str, list[dict[str, Any]]]:
    if not portfolio_ids:
        return {}
    from sqlalchemy import desc

    subq = (
        select(
            OrmPortfolioSnapshot.portfolio_id,
            OrmPortfolioSnapshot.timestamp,
            OrmPortfolioSnapshot.equity,
            func.row_number()
            .over(
                partition_by=OrmPortfolioSnapshot.portfolio_id,
                order_by=desc(OrmPortfolioSnapshot.timestamp),
            )
            .label("rn"),
        )
        .where(OrmPortfolioSnapshot.portfolio_id.in_(_uuids(portfolio_ids)))
        .subquery()
    )
    rows = store.session.execute(
        select(subq.c.portfolio_id, subq.c.timestamp, subq.c.equity)
        .where(subq.c.rn <= limit_per_portfolio)
        .order_by(subq.c.portfolio_id, subq.c.timestamp)
    ).all()
    out: dict[str, list[dict[str, Any]]] = {pid: [] for pid in portfolio_ids}
    for portfolio_id, ts, equity in rows:
        pid = str(portfolio_id)
        out.setdefault(pid, []).append(
            {"date": ts.isoformat(), "equity": float(equity)}
        )
    return out


def sum_realized_pnl_for_portfolios(store: TradingStore, portfolio_ids: list[str]) -> Decimal:
    if not portfolio_ids:
        return Decimal("0")
    total = store.session.scalar(
        select(func.coalesce(func.sum(OrmTrade.realized_pnl), 0)).where(
            OrmTrade.portfolio_id.in_(_uuids(portfolio_ids)),
            OrmTrade.backtest_run_id.is_(None),
        )
    )
    return Decimal(str(total or 0))
