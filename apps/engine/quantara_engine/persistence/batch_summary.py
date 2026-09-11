"""Batched SQL aggregates for competition portfolio dashboards."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import func, select

from quantara_engine.domain.types import Direction, Position
from quantara_engine.models.enums import FillSide as OrmFillSide
from quantara_engine.models.enums import PositionStatus as OrmPositionStatus
from quantara_engine.models.instruments import Candle as OrmCandle
from quantara_engine.models.instruments import Instrument as OrmInstrument
from quantara_engine.models.portfolio import PortfolioSnapshot as OrmPortfolioSnapshot
from quantara_engine.models.trading import Fill as OrmFill
from quantara_engine.models.trading import Order as OrmOrder
from quantara_engine.models.trading import OrderIntent as OrmOrderIntent
from quantara_engine.models.trading import Position as OrmPosition
from quantara_engine.models.trading import Trade as OrmTrade
from quantara_engine.models.portfolio import Portfolio as OrmPortfolio

if TYPE_CHECKING:
    from quantara_engine.persistence.store import TradingStore


def _uuids(ids: list[str]) -> list[uuid.UUID]:
    out: list[uuid.UUID] = []
    for i in ids:
        try:
            out.append(uuid.UUID(str(i)))
        except ValueError:
            continue
    return out


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


@dataclass(frozen=True)
class CompetitionExposureRiskSummary:
    total_open_exposure: Decimal | None
    total_open_risk_usd: Decimal | None
    total_equity: Decimal
    open_risk_pct: float | None
    open_position_count: int = 0
    risk_found_count: int = 0
    risk_missing_count: int = 0
    risk_zero_valid_count: int = 0
    exposure_missing_count: int = 0
    exposure_available: bool = True
    total_remaining_sl_risk_usd: Decimal | None = None
    projected_equity_at_stops: Decimal | None = None


@dataclass(frozen=True)
class AssetExposureRiskMetrics:
    open_exposure: Decimal | None
    open_risk_usd: Decimal | None
    asset_allocated_equity: Decimal
    open_risk_pct: float | None
    global_risk_cap_pct: float
    open_risk_missing_count: int = 0


def batch_trade_metrics(store: TradingStore, portfolio_ids: list[str]) -> dict[str, TradeBatchMetrics]:
    if not portfolio_ids:
        return {}
    from quantara_engine.competition.paper_run import trade_scope_clause

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
            trade_scope_clause(store),
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
    from quantara_engine.competition.paper_run import position_scope_clause

    ids = _uuids(portfolio_ids)
    rows = store.session.scalars(
        select(OrmPosition).where(
            OrmPosition.portfolio_id.in_(ids),
            OrmPosition.status == OrmPositionStatus.OPEN,
            position_scope_clause(store),
        )
    ).all()
    positions = [store._position_to_domain(row) for row in rows]
    store._hydrate_position_strategy_versions(positions)
    grouped: dict[str, list[Position]] = {pid: [] for pid in portfolio_ids}
    for pos in positions:
        grouped.setdefault(pos.portfolio_id, []).append(pos)
    return grouped


def batch_entry_actual_risk_by_position(
    store: TradingStore, position_ids: list[str]
) -> dict[str, Decimal]:
    """First entry-fill actual_risk_amount per open position — single query."""
    if not position_ids:
        return {}
    from quantara_engine.competition.paper_run import intent_scope_clause

    pids = _uuids(position_ids)
    rows = store.session.execute(
        select(OrmFill.position_id, OrmOrderIntent.actual_risk_amount, OrmFill.filled_at)
        .join(OrmOrder, OrmOrder.id == OrmFill.order_id)
        .join(OrmOrderIntent, OrmOrderIntent.id == OrmOrder.intent_id)
        .where(
            OrmFill.position_id.in_(pids),
            OrmFill.side == OrmFillSide.ENTRY,
            intent_scope_clause(store),
        )
        .order_by(OrmFill.position_id, OrmFill.filled_at)
    ).all()
    out: dict[str, Decimal] = {}
    for position_id, actual_risk, _filled_at in rows:
        pid = str(position_id)
        if pid not in out:
            out[pid] = Decimal(str(actual_risk or 0))
    return out


def _batch_instruments_by_id(
    store: TradingStore, instrument_ids: list[str]
) -> dict[str, Instrument]:
    if not instrument_ids:
        return {}
    rows = store.session.scalars(
        select(OrmInstrument).where(OrmInstrument.id.in_(_uuids(instrument_ids)))
    ).all()
    return {str(row.id): store._instrument_to_domain(row) for row in rows}


def _position_mark_price(current_price: Decimal | None, entry_price: Decimal | None) -> Decimal:
    mark = Decimal(str(current_price or 0))
    if mark > 0:
        return mark
    return Decimal(str(entry_price or 0))


def _position_exposure_usd(
    *,
    quantity: Decimal,
    mark: Decimal,
    instrument: Instrument | None,
    fx_rates: Any,
) -> Decimal | None:
    """Account-currency (USD) notional for one open position."""
    if instrument is None or mark <= 0:
        return None
    from quantara_engine.portfolio.currency import normalize_quote_currency

    qty = abs(Decimal(str(quantity or 0)))
    if qty <= 0:
        return None
    quote = normalize_quote_currency(instrument.quote_currency)
    try:
        return fx_rates.quote_notional_to_account(qty, mark, quote)
    except ValueError:
        return None


def _derive_position_open_risk(
    *,
    quantity: Decimal,
    direction: Any,
    entry_price: Decimal,
    stop_loss: Decimal,
    instrument: Instrument | None,
    fx_rates: Any,
) -> Decimal | None:
    """Canonical SL risk using the same sizing helper as live trading."""
    if instrument is None or entry_price <= 0 or stop_loss <= 0:
        return None
    qty = abs(Decimal(str(quantity or 0)))
    if qty <= 0:
        return None
    from quantara_engine.risk.sizing import _expected_risk_at_quantity

    dir_value = direction.value if hasattr(direction, "value") else str(direction)
    try:
        return _expected_risk_at_quantity(
            qty,
            direction=Direction(dir_value),
            entry_reference=entry_price,
            stop_loss=stop_loss,
            instrument=instrument,
            fx_rates=fx_rates,
            execution_assumptions=None,
        )
    except (ValueError, ZeroDivisionError, TypeError):
        return None


def _resolve_position_open_risk(
    *,
    position_id: str,
    quantity: Decimal,
    direction: Any,
    entry_price: Decimal,
    stop_loss: Decimal,
    instrument: Instrument | None,
    intent_risk: Decimal | None,
    fx_rates: Any,
) -> tuple[Decimal | None, str]:
    if intent_risk is not None:
        return intent_risk, "intent"
    derived = _derive_position_open_risk(
        quantity=quantity,
        direction=direction,
        entry_price=entry_price,
        stop_loss=stop_loss,
        instrument=instrument,
        fx_rates=fx_rates,
    )
    if derived is not None:
        return derived, "derived"
    return None, "missing"


def _derive_remaining_sl_risk(
    *,
    quantity: Decimal,
    direction: Any,
    mark_price: Decimal,
    stop_loss: Decimal,
    instrument: Instrument | None,
    fx_rates: Any,
) -> Decimal | None:
    """Risk from current mark to stop (remaining open risk if all stops hit now)."""
    if instrument is None or mark_price <= 0 or stop_loss <= 0:
        return None
    qty = abs(Decimal(str(quantity or 0)))
    if qty <= 0:
        return None
    from quantara_engine.risk.sizing import _expected_risk_at_quantity

    dir_value = direction.value if hasattr(direction, "value") else str(direction)
    try:
        return _expected_risk_at_quantity(
            qty,
            direction=Direction(dir_value),
            entry_reference=mark_price,
            stop_loss=stop_loss,
            instrument=instrument,
            fx_rates=fx_rates,
            execution_assumptions=None,
        )
    except (ValueError, ZeroDivisionError, TypeError):
        return None


def batch_portfolio_equity(
    store: TradingStore, portfolio_ids: list[str]
) -> dict[str, Decimal]:
    if not portfolio_ids:
        return {}
    rows = store.session.execute(
        select(OrmPortfolio.id, OrmPortfolio.equity).where(
            OrmPortfolio.id.in_(_uuids(portfolio_ids))
        )
    ).all()
    return {str(portfolio_id): Decimal(str(equity or 0)) for portfolio_id, equity in rows}


def batch_competition_exposure_risk_summary(
    store: TradingStore,
    portfolio_ids: list[str],
    *,
    symbol_by_instrument_id: dict[str, str],
) -> tuple[CompetitionExposureRiskSummary, dict[str, AssetExposureRiskMetrics]]:
    """Batched open exposure + canonical SL risk for competition dashboards."""
    from quantara_engine.competition.asset_equity import (
        nominal_asset_allocated_equity,
        portfolio_ids_for_symbol,
    )
    from quantara_engine.market_data.active_universe import ACTIVE_DB_SYMBOLS
    from quantara_engine.risk.open_risk_guard import DEFAULT_OPEN_RISK_LIMITS

    cap_pct = float(DEFAULT_OPEN_RISK_LIMITS.max_global_asset_open_risk_pct or 0)
    empty_summary = CompetitionExposureRiskSummary(
        total_open_exposure=Decimal("0"),
        total_open_risk_usd=Decimal("0"),
        total_equity=Decimal("0"),
        open_risk_pct=0.0,
        open_position_count=0,
    )
    if not portfolio_ids:
        return empty_summary, {}

    equity_by_portfolio = batch_portfolio_equity(store, portfolio_ids)
    asset_allocated_equity: dict[str, Decimal] = {}
    for db_symbol in ACTIVE_DB_SYMBOLS:
        allocated_ids = portfolio_ids_for_symbol(db_symbol)
        total = sum(equity_by_portfolio.get(pid, Decimal("0")) for pid in allocated_ids)
        asset_allocated_equity[db_symbol.upper()] = (
            total if total > 0 else nominal_asset_allocated_equity(db_symbol)
        )

    total_equity = sum(equity_by_portfolio.values(), Decimal("0"))
    if total_equity <= 0:
        total_equity = sum(asset_allocated_equity.values(), Decimal("0"))

    open_rows = store.session.execute(
        select(
            OrmPosition.id,
            OrmPosition.instrument_id,
            OrmPosition.quantity,
            OrmPosition.current_price,
            OrmPosition.entry_price,
            OrmPosition.stop_loss,
            OrmPosition.direction,
        ).where(
            OrmPosition.portfolio_id.in_(_uuids(portfolio_ids)),
            OrmPosition.status == OrmPositionStatus.OPEN,
        )
    ).all()
    if not open_rows:
        summary = CompetitionExposureRiskSummary(
            total_open_exposure=Decimal("0"),
            total_open_risk_usd=Decimal("0"),
            total_equity=total_equity,
            open_risk_pct=0.0,
            open_position_count=0,
        )
        return summary, {}

    position_ids = [str(row[0]) for row in open_rows]
    intent_risk_by_position = batch_entry_actual_risk_by_position(store, position_ids)
    instrument_ids = list({str(row[1]) for row in open_rows})
    instruments_by_id = _batch_instruments_by_id(store, instrument_ids)
    from quantara_engine.portfolio.currency import quote_currencies_for_instruments, resolve_dashboard_fx_rates

    fx_rates = resolve_dashboard_fx_rates(
        store, quote_currencies_for_instruments(instruments_by_id.values())
    )

    exposure_by_instrument: dict[str, Decimal] = {}
    exposure_missing_by_instrument: dict[str, int] = {}
    risk_known_by_instrument: dict[str, Decimal] = {}
    risk_missing_by_instrument: dict[str, int] = {}
    remaining_risk_known_by_instrument: dict[str, Decimal] = {}
    remaining_risk_missing_by_instrument: dict[str, int] = {}
    risk_found_count = 0
    risk_missing_count = 0
    risk_zero_valid_count = 0
    remaining_risk_missing_count = 0
    exposure_missing_count = 0

    for (
        position_id,
        instrument_id,
        quantity,
        current_price,
        entry_price,
        stop_loss,
        direction,
    ) in open_rows:
        iid = str(instrument_id)
        pid = str(position_id)
        inst = instruments_by_id.get(iid)
        mark = _position_mark_price(current_price, entry_price)
        qty = Decimal(str(quantity or 0))
        usd_exposure = _position_exposure_usd(
            quantity=qty,
            mark=mark,
            instrument=inst,
            fx_rates=fx_rates,
        )
        if usd_exposure is None:
            exposure_missing_count += 1
            exposure_missing_by_instrument[iid] = exposure_missing_by_instrument.get(iid, 0) + 1
        else:
            exposure_by_instrument[iid] = exposure_by_instrument.get(iid, Decimal("0")) + usd_exposure

        intent_risk = intent_risk_by_position.get(pid)
        resolved_risk, source = _resolve_position_open_risk(
            position_id=pid,
            quantity=qty,
            direction=direction,
            entry_price=Decimal(str(entry_price or 0)),
            stop_loss=Decimal(str(stop_loss or 0)),
            instrument=inst,
            intent_risk=intent_risk,
            fx_rates=fx_rates,
        )
        remaining_risk = _derive_remaining_sl_risk(
            quantity=qty,
            direction=direction,
            mark_price=mark,
            stop_loss=Decimal(str(stop_loss or 0)),
            instrument=inst,
            fx_rates=fx_rates,
        )
        if remaining_risk is None:
            remaining_risk_missing_count += 1
            remaining_risk_missing_by_instrument[iid] = (
                remaining_risk_missing_by_instrument.get(iid, 0) + 1
            )
        else:
            remaining_risk_known_by_instrument[iid] = (
                remaining_risk_known_by_instrument.get(iid, Decimal("0")) + remaining_risk
            )

        if source == "missing" or resolved_risk is None:
            risk_missing_count += 1
            risk_missing_by_instrument[iid] = risk_missing_by_instrument.get(iid, 0) + 1
            continue
        if resolved_risk <= 0:
            risk_zero_valid_count += 1
        else:
            risk_found_count += 1
        risk_known_by_instrument[iid] = risk_known_by_instrument.get(iid, Decimal("0")) + resolved_risk

    total_open_exposure: Decimal | None
    if exposure_missing_count > 0:
        total_open_exposure = None
    else:
        total_open_exposure = sum(exposure_by_instrument.values(), Decimal("0")).quantize(
            Decimal("0.01")
        )
    total_open_risk_usd: Decimal | None
    open_risk_pct: float | None
    if risk_missing_count > 0:
        total_open_risk_usd = None
        open_risk_pct = None
    else:
        total_open_risk_usd = sum(risk_known_by_instrument.values(), Decimal("0")).quantize(
            Decimal("0.01")
        )
        open_risk_pct = (
            float(total_open_risk_usd / total_equity * Decimal("100"))
            if total_equity > 0
            else 0.0
        )
        open_risk_pct = round(open_risk_pct, 2)

    total_remaining_sl_risk_usd: Decimal | None
    projected_equity_at_stops: Decimal | None
    if remaining_risk_missing_count > 0:
        total_remaining_sl_risk_usd = None
        projected_equity_at_stops = None
    else:
        total_remaining_sl_risk_usd = sum(
            remaining_risk_known_by_instrument.values(), Decimal("0")
        ).quantize(Decimal("0.01"))
        projected_equity_at_stops = (total_equity - total_remaining_sl_risk_usd).quantize(
            Decimal("0.01")
        )

    summary = CompetitionExposureRiskSummary(
        total_open_exposure=total_open_exposure,
        total_open_risk_usd=total_open_risk_usd,
        total_equity=total_equity,
        open_risk_pct=open_risk_pct,
        open_position_count=len(open_rows),
        risk_found_count=risk_found_count,
        risk_missing_count=risk_missing_count,
        risk_zero_valid_count=risk_zero_valid_count,
        exposure_missing_count=exposure_missing_count,
        exposure_available=exposure_missing_count == 0,
        total_remaining_sl_risk_usd=total_remaining_sl_risk_usd,
        projected_equity_at_stops=projected_equity_at_stops,
    )

    instrument_ids_with_positions = {str(row[1]) for row in open_rows}
    by_instrument: dict[str, AssetExposureRiskMetrics] = {}
    for iid in instrument_ids_with_positions:
        db_symbol = symbol_by_instrument_id.get(iid, "").upper()
        allocated = asset_allocated_equity.get(db_symbol, Decimal("0"))
        risk_missing = risk_missing_by_instrument.get(iid, 0)
        exposure_missing = exposure_missing_by_instrument.get(iid, 0)
        known_risk = risk_known_by_instrument.get(iid, Decimal("0")).quantize(Decimal("0.01"))
        if exposure_missing > 0:
            exposure_val: Decimal | None = None
        else:
            exposure_val = exposure_by_instrument.get(iid, Decimal("0")).quantize(Decimal("0.01"))
        if risk_missing > 0:
            risk_usd: Decimal | None = None
            risk_pct: float | None = None
        else:
            risk_usd = known_risk
            risk_pct = float(known_risk / allocated * Decimal("100")) if allocated > 0 else 0.0
            risk_pct = round(risk_pct, 2)
        by_instrument[iid] = AssetExposureRiskMetrics(
            open_exposure=exposure_val,
            open_risk_usd=risk_usd,
            asset_allocated_equity=allocated,
            open_risk_pct=risk_pct,
            global_risk_cap_pct=cap_pct,
            open_risk_missing_count=risk_missing,
        )
    return summary, by_instrument


def batch_asset_metrics(
    store: TradingStore, portfolio_ids: list[str]
) -> dict[str, AssetBatchMetrics]:
    if not portfolio_ids:
        return {}
    from quantara_engine.competition.paper_run import position_scope_clause, trade_scope_clause

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
            position_scope_clause(store),
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
            trade_scope_clause(store),
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
    """Latest close per instrument."""
    if not instrument_ids:
        return {}
    from sqlalchemy import desc

    ids = _uuids(instrument_ids)
    dialect = store.session.get_bind().dialect.name
    if dialect == "postgresql":
        rows = store.session.execute(
            select(OrmCandle.instrument_id, OrmCandle.close)
            .distinct(OrmCandle.instrument_id)
            .where(
                OrmCandle.instrument_id.in_(ids),
                OrmCandle.timeframe == timeframe,
            )
            .order_by(OrmCandle.instrument_id, desc(OrmCandle.timestamp))
        ).all()
    else:
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
                OrmCandle.instrument_id.in_(ids),
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
    from quantara_engine.competition.paper_run import trade_scope_clause

    total = store.session.scalar(
        select(func.coalesce(func.sum(OrmTrade.realized_pnl), 0)).where(
            OrmTrade.portfolio_id.in_(_uuids(portfolio_ids)),
            trade_scope_clause(store),
        )
    )
    return Decimal(str(total or 0))
