"""API routes — Engine REST API v1 (PostgreSQL via TradingStore)."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from quantara_engine.analytics.service import AnalyticsService
from quantara_engine.api.deps import get_store, verify_api_key
from quantara_engine.api.state import runtime_cache
from quantara_engine.backtesting.runner import BacktestRun, BacktestRunner
from quantara_engine.competition.constants import (
    PORTFOLIO_DEF_BY_ID,
    RISK_SLUG_HE,
    TIMEFRAME_GROUP_TITLE_HE,
    TIMEFRAME_HE,
)
from quantara_engine.competition.orb_service import build_orb_status
from quantara_engine.competition.service import (
    build_competition_equity_curves,
    build_competition_response,
)
from quantara_engine.core.config import settings
from quantara_engine.domain.types import ExecutionAssumptions, Mode, PortfolioStatus
from quantara_engine.market_data.credits import status_payload as credit_status_payload
from quantara_engine.market_data.factory import get_market_data_provider
from quantara_engine.market_data.spot_price import (
    read_spot_snapshot,
    resolve_spot_snapshot,
    spot_age_minutes,
    spot_response_fields,
)
from quantara_engine.persistence.store import TradingStore
from quantara_engine.strategies.registry import get, list_all

router = APIRouter(prefix="/api/v1", dependencies=[Depends(verify_api_key)])

StoreDep = Annotated[TradingStore, Depends(get_store)]


def _drawdown_pct(equity: Decimal, peak_equity: Decimal) -> float:
    if peak_equity <= 0:
        return 0.0
    return float((peak_equity - equity) / peak_equity * 100)


def _daily_pnl_paper(store: TradingStore, portfolio_id: str, current_equity: Decimal) -> Decimal:
    """Monetary P&L since UTC day start from paper snapshots only."""
    today_start = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    start_equity = store.get_start_of_day_equity(portfolio_id, today_start)
    if start_equity is None:
        return Decimal("0")
    return (current_equity - start_equity).quantize(Decimal("0.01"))


def _resolve_portfolio(store: TradingStore, portfolio_ref: str = "competition"):
    try:
        return store.resolve_paper_portfolio(portfolio_ref)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


def _is_combined_competition_portfolio(portfolio_id: str) -> bool:
    from quantara_engine.competition.constants import ACTIVE_COMPETITION_EXPERIMENT_ID

    return portfolio_id == ACTIVE_COMPETITION_EXPERIMENT_ID


def _portfolio_ui(store: TradingStore, portfolio_ref: str = "competition") -> dict:
    portfolio = _resolve_portfolio(store, portfolio_ref)
    settings = store.get_settings_dict()
    combined = _is_combined_competition_portfolio(portfolio.id)
    if combined:
        realized = store.sum_competition_realized_pnl()
        daily = Decimal("0")
        for entry in store.list_competition_entries():
            daily += _daily_pnl_paper(store, entry["portfolio"].id, entry["portfolio"].equity)
    else:
        realized = store.sum_realized_pnl(portfolio.id)
        daily = _daily_pnl_paper(store, portfolio.id, portfolio.equity)
    risk_slug = store.get_portfolio_risk_slug(portfolio.id) or settings.get(
        "default_risk_profile", "balanced"
    )
    portfolio_def = PORTFOLIO_DEF_BY_ID.get(portfolio.id)
    instance = store.get_paper_strategy_instance(portfolio.id)

    payload = {
        "id": portfolio.id,
        "name": portfolio_def.name_he if portfolio_def else portfolio.name,
        "equity": float(portfolio.equity),
        "cash_balance": float(portfolio.balance),
        "unrealized_pnl": float(portfolio.unrealized_pnl),
        "realized_pnl": float(realized),
        "daily_pnl": float(daily),
        "peak_equity": float(portfolio.peak_equity),
        "current_drawdown_pct": _drawdown_pct(portfolio.equity, portfolio.peak_equity),
        "risk_profile": risk_slug,
        "mode": portfolio.mode.value,
        "initial_capital": float(portfolio.initial_capital),
    }
    if portfolio_def:
        payload["competition"] = {
            "timeframe": portfolio_def.timeframe,
            "timeframe_he": TIMEFRAME_HE.get(portfolio_def.timeframe, portfolio_def.timeframe),
            "risk_slug": portfolio_def.risk_slug,
            "risk_name_he": RISK_SLUG_HE.get(portfolio_def.risk_slug, portfolio.name),
            "risk_per_trade_pct": float(
                store.get_risk_profile_by_slug(portfolio_def.risk_slug).risk_per_trade_pct
                if store.get_risk_profile_by_slug(portfolio_def.risk_slug)
                else 0
            ),
        }
    elif instance:
        payload["timeframe"] = instance.timeframe
    return payload


def _strategy_label(store: TradingStore, strategy_version_id: str) -> tuple[str, str]:
    version = store.resolve_strategy_version_ref(strategy_version_id)
    if version:
        name = version.get("strategy_name") or version.get("strategy_slug", "").replace("-", " ").title()
        return name or "Gold Trend Pullback", version.get("version", "1.0.0")
    return "Gold Trend Pullback", "1.0.0"


def _strategy_label_from_instance(store: TradingStore, strategy_instance_id: str) -> tuple[str, str]:
    return _batch_strategy_labels_from_instances(store, [strategy_instance_id]).get(
        strategy_instance_id, ("Gold Trend Pullback", "1.0.0")
    )


def _batch_strategy_labels_from_instances(
    store: TradingStore, strategy_instance_ids: list[str]
) -> dict[str, tuple[str, str]]:
    if not strategy_instance_ids:
        return {}
    from quantara_engine.models.portfolio import StrategyInstance as OrmStrategyInstance
    from quantara_engine.models.strategies import Strategy as OrmStrategy
    from quantara_engine.models.strategies import StrategyVersion as OrmStrategyVersion

    ids = [uuid.UUID(i) for i in strategy_instance_ids]
    rows = store.session.execute(
        select(OrmStrategyInstance.id, OrmStrategy.name, OrmStrategyVersion.version)
        .join(
            OrmStrategyVersion,
            OrmStrategyInstance.strategy_version_id == OrmStrategyVersion.id,
        )
        .join(OrmStrategy, OrmStrategyVersion.strategy_id == OrmStrategy.id)
        .where(OrmStrategyInstance.id.in_(ids))
    ).all()
    return {
        str(instance_id): (name, version)
        for instance_id, name, version in rows
    }


@router.get("/health")
def health():
    return {
        "status": "ok",
        "service": "quantara-engine",
        "api_version": "multi-provider-8asset",
        "features": {
            "analytics_assets": True,
            "multi_market_data_status": True,
        },
    }


@router.get("/instruments")
def instruments(store: StoreDep):
    return [
        {
            "id": i.id,
            "symbol": i.symbol,
            "name": i.name,
            "quantity_step": str(i.quantity_step),
            "min_quantity": str(i.min_quantity),
        }
        for i in store.list_instruments()
    ]


@router.get("/candles")
def candles(
    store: StoreDep,
    instrument_id: str = "xauusd",
    timeframe: str = "1h",
    limit: int = 100,
):
    instrument = store.get_instrument_by_symbol(instrument_id.upper().replace("/", ""))
    if not instrument:
        instrument = store.get_instrument_by_symbol("XAUUSD")
    if not instrument:
        raise HTTPException(404, "Instrument not found")

    rows = store.list_candles(instrument.id, timeframe, limit=limit)
    if len(rows) < limit and settings.market_data_provider == "mock":
        provider = get_market_data_provider("mock")
        generated = provider.generate_candles(instrument.id, timeframe, limit)
        for candle in generated:
            store.upsert_candle(candle)
        rows = store.list_candles(instrument.id, timeframe, limit=limit)

    return [
        {
            "time": c.timestamp.isoformat(),
            "open": float(c.open),
            "high": float(c.high),
            "low": float(c.low),
            "close": float(c.close),
            "volume": float(c.volume) if c.volume else None,
        }
        for c in rows
    ]


@router.get("/candles/latest")
def candles_latest(
    store: StoreDep,
    instrument: str = "XAU/USD",
    timeframe: str = "1h",
):
    symbol = instrument.upper().replace("/", "")
    inst = store.get_instrument_by_symbol(symbol) or store.get_instrument_by_symbol("XAUUSD")
    if not inst:
        raise HTTPException(404, "Instrument not found")

    spot = resolve_spot_snapshot(store, allow_fetch=False)
    if spot:
        fields = spot_response_fields(spot)
        return {
            "instrument": instrument,
            **fields,
        }

    rows = store.list_recent_candles(inst.id, "5m", limit=50)
    if len(rows) < 2 and settings.market_data_provider == "mock":
        provider = get_market_data_provider("mock")
        generated = provider.generate_candles(inst.id, timeframe, 50)
        for candle in generated:
            store.upsert_candle(candle)
        rows = store.list_recent_candles(inst.id, timeframe, limit=50)
    if len(rows) < 1:
        raise HTTPException(404, "No candle data available")

    last = rows[-1]
    if len(rows) >= 2:
        prev = rows[-2]
        change = float(last.close - prev.close)
        change_pct = (change / float(prev.close) * 100) if prev.close else 0.0
    else:
        change = 0.0
        change_pct = 0.0
    age_minutes = max(
        0.0,
        (datetime.now(timezone.utc) - last.timestamp).total_seconds() / 60,
    )
    return {
        "instrument": instrument,
        "price": float(last.close),
        "change": change,
        "change_pct": change_pct,
        "last_update": last.timestamp.isoformat(),
        "timeframe": timeframe,
        "price_source": f"candle_{timeframe}",
        "data_age_minutes": round(age_minutes, 1),
        "is_stale": age_minutes > 60,
    }


@router.get("/market-data/status")
def market_data_status(store: StoreDep):
    from quantara_engine.market_data.provider_budgets import all_provider_status
    from quantara_engine.market_data.registry import list_target_assets
    from quantara_engine.market_data.sessions import (
        is_data_stale_while_session_open,
        session_allows_entries,
    )
    from quantara_engine.api.dashboard_cache import dashboard_candle_bundle

    worker_raw = store.get_settings_dict().get("worker_status:data_fetcher") or {}
    now = datetime.now(timezone.utc)
    asset_rows: list[dict] = []
    healthy = True
    assets = list(list_target_assets())
    instrument_by_symbol = {
        asset.db_symbol: store.get_instrument_by_symbol(asset.db_symbol) for asset in assets
    }
    instrument_ids = [inst.id for inst in instrument_by_symbol.values() if inst]
    candle_bundle = dashboard_candle_bundle(store, instrument_ids)
    candle_counts = candle_bundle["counts"]
    last_candles = candle_bundle["last_candles"]
    latest_closes = candle_bundle["latest_closes"]

    for asset in assets:
        inst = instrument_by_symbol.get(asset.db_symbol)
        counts: dict[str, int] = {"5m": 0, "15m": 0, "1h": 0}
        last_candle = None
        latest_price = None
        stale = True
        session_status = "unknown"
        if inst:
            counts = candle_counts.get(inst.id, counts)
            last_candle = last_candles.get(inst.id)
            if inst.id in latest_closes:
                latest_price = float(latest_closes[inst.id])
            if last_candle:
                session_status = (
                    "open"
                    if session_allows_entries(asset.trading_sessions, now)
                    else "closed"
                )
                stale = is_data_stale_while_session_open(
                    asset.trading_sessions, last_candle, now
                )
        asset_health = (worker_raw.get("assets") or {}).get(asset.db_symbol, {})
        status = asset_health.get("status") or ("stale" if stale else "healthy")
        if session_status == "closed" and last_candle and status not in ("error", "blocked"):
            status = "deferred"
        if not last_candle and asset.primary_provider.value == "twelvedata":
            status = "blocked"
        if status in ("error", "stale", "blocked"):
            healthy = False
        asset_rows.append(
            {
                "symbol": asset.display_symbol,
                "db_symbol": asset.db_symbol,
                "provider": asset.primary_provider.value,
                "secondary_provider": (
                    asset.secondary_provider.value if asset.secondary_provider else None
                ),
                "status": status,
                "last_candle": last_candle.isoformat() if last_candle else None,
                "latest_price": latest_price,
                "session_status": session_status,
                "candle_counts": counts,
                "stale": stale,
                "session_closed": session_status == "closed",
                "timeframes_available": {
                    "5m": counts.get("5m", 0) > 0,
                    "15m": counts.get("15m", 0) > 0,
                    "1h": counts.get("1h", 0) > 0,
                },
            }
        )

    spot = read_spot_snapshot(store)
    xau = store.get_instrument_by_symbol("XAUUSD")
    last_fetch = None
    if spot:
        last_fetch = datetime.fromisoformat(spot.get("fetched_at") or spot.get("updated_at"))  # type: ignore[arg-type]
    elif xau:
        last_fetch = store.latest_candle_timestamp(xau.id, "1h")

    return {
        "healthy": healthy,
        "provider": "multi",
        "source": "multi",
        "last_fetch": last_fetch.isoformat() if last_fetch else None,
        "assets": asset_rows,
        "providers": all_provider_status(store),
        "worker": worker_raw,
        "spot_source": spot.get("source") if spot else None,
        "spot_age_minutes": round(spot_age_minutes(spot), 1) if spot else None,
    }


@router.get("/analytics/assets")
def analytics_assets(store: StoreDep):
    """Per-asset market + competition P&L summary for the Home dashboard."""
    from quantara_engine.market_data.polling import STRATEGY_MIN_CANDLES
    from quantara_engine.market_data.registry import list_target_assets
    from quantara_engine.market_data.sessions import (
        is_data_stale_while_session_open,
        session_allows_entries,
    )
    from quantara_engine.api.dashboard_cache import dashboard_candle_bundle

    robot_a, robot_b, combined = store.list_all_competition_entries()
    if not combined:
        raise HTTPException(404, "Competition not configured")

    now = datetime.now(timezone.utc)
    worker_raw = store.get_settings_dict().get("worker_status:data_fetcher") or {}
    rows: list[dict] = []

    portfolio_ids = [e["portfolio"].id for e in combined]
    asset_metrics = store.batch_asset_trading_metrics(portfolio_ids)
    assets = list_target_assets()
    instrument_by_symbol = {
        asset.db_symbol: store.get_instrument_by_symbol(asset.db_symbol) for asset in assets
    }
    symbol_by_instrument_id = {
        inst.id: asset.db_symbol
        for asset in assets
        if (inst := instrument_by_symbol.get(asset.db_symbol))
    }
    exposure_summary, exposure_by_instrument = store.batch_competition_exposure_risk_summary(
        portfolio_ids,
        symbol_by_instrument_id=symbol_by_instrument_id,
    )
    instrument_ids = [inst.id for inst in instrument_by_symbol.values() if inst]
    candle_bundle = dashboard_candle_bundle(store, instrument_ids)
    candle_counts = candle_bundle["counts"]
    last_candles = candle_bundle["last_candles"]
    latest_closes = candle_bundle["latest_closes"]

    for asset in assets:
        inst = instrument_by_symbol.get(asset.db_symbol)
        counts = {"5m": 0, "15m": 0, "1h": 0}
        last_candle = None
        latest_price = None
        stale = True
        session_status = "unknown"
        strategy_ready = {"5m": False, "15m": False, "1h": False}

        open_positions = 0
        closed_trades = 0
        realized_pnl = 0.0
        unrealized_pnl = 0.0

        if inst:
            counts = candle_counts.get(inst.id, counts)
            for tf in ("5m", "15m", "1h"):
                strategy_ready[tf] = counts.get(tf, 0) >= STRATEGY_MIN_CANDLES
            last_candle = last_candles.get(inst.id)
            if inst.id in latest_closes:
                latest_price = float(latest_closes[inst.id])
            if last_candle:
                session_status = (
                    "open"
                    if session_allows_entries(asset.trading_sessions, now)
                    else "closed"
                )
                stale = is_data_stale_while_session_open(
                    asset.trading_sessions, last_candle, now
                )

            metrics = asset_metrics.get(inst.id, {})
            open_positions = int(metrics.get("open_positions", 0))
            closed_trades = int(metrics.get("closed_trades", 0))
            realized_pnl = float(metrics.get("realized_pnl", 0.0))
            unrealized_pnl = float(metrics.get("unrealized_pnl", 0.0))

        risk_metrics = exposure_by_instrument.get(inst.id if inst else "", None)
        if open_positions > 0 and risk_metrics is not None:
            open_exposure = (
                float(risk_metrics.open_exposure)
                if risk_metrics.open_exposure is not None
                else None
            )
            open_risk_usd = (
                float(risk_metrics.open_risk_usd)
                if risk_metrics.open_risk_usd is not None
                else None
            )
            open_risk_pct = risk_metrics.open_risk_pct
        elif open_positions > 0:
            open_exposure = None
            open_risk_usd = None
            open_risk_pct = None
        else:
            open_exposure = (
                float(risk_metrics.open_exposure)
                if risk_metrics and risk_metrics.open_exposure is not None
                else 0.0
            )
            open_risk_usd = (
                float(risk_metrics.open_risk_usd)
                if risk_metrics and risk_metrics.open_risk_usd is not None
                else 0.0
            )
            open_risk_pct = risk_metrics.open_risk_pct if risk_metrics else 0.0
        global_risk_cap_pct = risk_metrics.global_risk_cap_pct if risk_metrics else 2.0

        asset_health = (worker_raw.get("assets") or {}).get(asset.db_symbol, {})
        data_status = asset_health.get("status") or ("stale" if stale else "healthy")
        if session_status == "closed" and last_candle and data_status not in ("error", "blocked"):
            data_status = "deferred"
        if not last_candle and asset.primary_provider.value == "twelvedata":
            data_status = "blocked"
        live_provider = asset_health.get("provider") or asset.primary_provider.value

        regime_payload = None
        if inst and counts.get("15m", 0) >= STRATEGY_MIN_CANDLES:
            from quantara_engine.market_regime.snapshots import classify_and_save

            candles_15m = store.list_candles(inst.id, "15m", limit=120)
            if candles_15m:
                snapshot = classify_and_save(store, inst, "15m", candles_15m)
                if snapshot:
                    regime_payload = {
                        "structure_regime": snapshot.structure_regime.value,
                        "volatility_regime": snapshot.volatility_regime.value,
                        "candle_time": candles_15m[-1].timestamp.isoformat(),
                    }

        rows.append(
            {
                "symbol": asset.display_symbol,
                "db_symbol": asset.db_symbol,
                "provider": live_provider,
                "latest_price": latest_price,
                "last_candle": last_candle.isoformat() if last_candle else None,
                "data_status": data_status,
                "stale": stale,
                "session_status": session_status,
                "session_closed": session_status == "closed",
                "candle_counts": counts,
                "timeframes_available": {
                    tf: counts[tf] > 0 for tf in ("5m", "15m", "1h")
                },
                "strategy_ready": strategy_ready,
                "open_positions": open_positions,
                "closed_trades": closed_trades,
                "realized_pnl": round(realized_pnl, 2),
                "unrealized_pnl": round(unrealized_pnl, 2),
                "total_pnl": round(realized_pnl + unrealized_pnl, 2),
                "open_exposure": round(open_exposure, 2) if open_exposure is not None else None,
                "open_risk_usd": round(open_risk_usd, 2) if open_risk_usd is not None else None,
                "open_risk_pct": round(open_risk_pct, 2) if open_risk_pct is not None else None,
                "global_risk_cap_pct": global_risk_cap_pct,
                "market_regime": regime_payload,
            }
        )

    open_exposure_usd = (
        float(exposure_summary.total_open_exposure)
        if exposure_summary.total_open_exposure is not None
        else None
    )
    summary_payload: dict[str, float | int | bool | None] = {
        "open_exposure": open_exposure_usd,
        "open_exposure_usd": open_exposure_usd,
        "open_risk_usd": (
            float(exposure_summary.total_open_risk_usd)
            if exposure_summary.total_open_risk_usd is not None
            else None
        ),
        "open_risk_pct": exposure_summary.open_risk_pct,
        "total_equity": float(exposure_summary.total_equity),
        "open_position_count": exposure_summary.open_position_count,
        "risk_found_count": exposure_summary.risk_found_count,
        "risk_missing_count": exposure_summary.risk_missing_count,
        "risk_zero_valid_count": exposure_summary.risk_zero_valid_count,
        "exposure_missing_count": exposure_summary.exposure_missing_count,
        "exposure_available": exposure_summary.exposure_available,
        "remaining_sl_risk_usd": (
            float(exposure_summary.total_remaining_sl_risk_usd)
            if exposure_summary.total_remaining_sl_risk_usd is not None
            else None
        ),
        "projected_equity_at_stops": (
            float(exposure_summary.projected_equity_at_stops)
            if exposure_summary.projected_equity_at_stops is not None
            else None
        ),
    }
    return {
        "assets_active": len(list_target_assets()),
        "summary": summary_payload,
        "assets": rows,
    }


@router.get("/portfolio")
def portfolio(store: StoreDep, portfolio_id: str = "competition"):
    return _portfolio_ui(store, portfolio_id)


@router.get("/broker/rejections")
def broker_rejections(store: StoreDep, limit: int = 100):
    """Recent broker pre-trade rejections (requires migration 0006)."""
    from sqlalchemy import text

    try:
        rows = store.session.execute(
            text(
                """
                SELECT created_at, symbol, quantity, reason, detail,
                       opportunity_key, strategy_portfolio_id::text AS portfolio_id
                FROM broker_order_rejections
                ORDER BY created_at DESC
                LIMIT :lim
                """
            ),
            {"lim": min(limit, 500)},
        ).mappings().all()
    except Exception:
        return {"rejections": [], "available": False}
    return {
        "available": True,
        "rejections": [
            {
                "timestamp": str(r["created_at"]),
                "symbol": r["symbol"],
                "quantity": float(r["quantity"]),
                "reason": r["reason"],
                "detail": r.get("detail"),
                "opportunity_key": r.get("opportunity_key"),
                "portfolio_id": r.get("portfolio_id"),
            }
            for r in rows
        ],
    }


@router.get("/broker/account")
def broker_account_summary(store: StoreDep):
    """Canonical paper broker account (shared across 160 strategy portfolios)."""
    from quantara_engine.broker.physical_risk import compute_physical_broker_risk
    from quantara_engine.broker.state_builder import build_competition_broker_account

    from quantara_engine.broker.execution_service import BrokerExecutionService

    account = build_competition_broker_account(store)
    row = BrokerExecutionService(store).get_account_row() or {}
    gross_realized = float(row.get("gross_realized_pnl") or account.realized_pnl)
    fees_paid = float(row.get("fees_paid") or 0)
    net_realized = float(account.realized_pnl)
    physical_risk = compute_physical_broker_risk(store)
    return {
        "profile": account.profile_slug,
        "account_state": account.account_state.value,
        "cash": float(account.cash),
        "balance": float(account.balance),
        "equity": float(account.equity),
        "realized_pnl": net_realized,
        "gross_realized_pnl": gross_realized,
        "fees_paid": fees_paid,
        "net_realized_pnl": net_realized,
        "unrealized_pnl": float(account.unrealized_pnl),
        "available_margin": float(account.available_margin),
        "spot_crypto_cash": float(account.spot_crypto_cash),
        "buying_power": float(account.available_margin),
        "initial_margin_used": float(account.initial_margin_used),
        "maintenance_margin_required": float(account.maintenance_margin_required),
        "free_margin": float(account.free_margin),
        "position_mode": "netting",
        "hedging_supported": False,
        "margin_level_pct": float(account.margin_level_pct) if account.margin_level_pct else None,
        "gross_exposure": float(account.gross_exposure),
        "net_exposure": float(account.net_exposure),
        "gross_leverage": float(account.gross_leverage),
        "net_leverage": float(account.net_leverage),
        "broker_positions": [
            {
                "symbol": p.symbol,
                "net_quantity": float(p.net_quantity),
                "average_price": float(p.average_price),
                "mark_price": float(p.mark_price),
                "unrealized_pnl": float(p.unrealized_pnl),
            }
            for p in account.positions.values()
        ],
        "physical_remaining_sl_risk_usd": (
            float(physical_risk["physical_remaining_sl_risk_usd"])
            if physical_risk["physical_remaining_sl_risk_usd"] is not None
            else None
        ),
        "projected_broker_equity_at_stops": (
            float(physical_risk["projected_broker_equity_at_stops"])
            if physical_risk["projected_broker_equity_at_stops"] is not None
            else None
        ),
        "physical_risk_complete": bool(physical_risk.get("physical_risk_complete", True)),
        "physical_risk_missing_count": int(physical_risk.get("physical_risk_missing_count") or 0),
    }


@router.get("/portfolio/risk-status")
def portfolio_risk_status(store: StoreDep, portfolio_id: str = "competition"):
    portfolio = _resolve_portfolio(store, portfolio_id)
    if _is_combined_competition_portfolio(portfolio.id):
        entries = store.list_competition_entries()
        exposure_pct = (
            float(portfolio.exposure_notional / portfolio.equity * 100)
            if portfolio.equity > 0
            else 0
        )
        return {
            "profile": "competition",
            "halted": portfolio.status == PortfolioStatus.HALTED,
            "exposure_pct": round(exposure_pct, 2),
            "halt_reason": portfolio.halt_reason,
            "portfolio_count": len(entries),
        }
    settings = store.get_settings_dict()
    risk_slug = store.get_portfolio_risk_slug(portfolio.id) or settings.get(
        "default_risk_profile", "balanced"
    )
    exposure_pct = (
        float(portfolio.exposure_notional / portfolio.equity * 100)
        if portfolio.equity > 0
        else 0
    )
    return {
        "profile": risk_slug,
        "halted": portfolio.status == PortfolioStatus.HALTED,
        "exposure_pct": round(exposure_pct, 2),
        "halt_reason": portfolio.halt_reason,
    }


@router.get("/portfolio/snapshots")
def portfolio_snapshots(
    store: StoreDep,
    portfolio_id: str = "competition",
    limit: int = 30,
):
    portfolio = _resolve_portfolio(store, portfolio_id)
    if _is_combined_competition_portfolio(portfolio.id):
        return []
    snaps = store.list_snapshots(portfolio.id, limit=limit)
    return [
        {
            "id": f"snap-{i}",
            "timestamp": s.timestamp.isoformat(),
            "equity": float(s.equity),
            "cash_balance": float(s.balance),
            "unrealized_pnl": float(s.unrealized_pnl),
            "realized_pnl": float(s.balance - portfolio.initial_capital),
        }
        for i, s in enumerate(reversed(snaps))
    ]


@router.get("/positions")
def positions(
    store: StoreDep,
    portfolio_id: str = "competition",
    status: str = "open",
):
    portfolio = _resolve_portfolio(store, portfolio_id)
    if _is_combined_competition_portfolio(portfolio.id) or portfolio_id in (
        "competition",
        "paper-main",
        "paper",
        "combined",
    ):
        if status == "open":
            items = store.list_competition_positions(open_only=True)
        elif status == "all":
            items = store.list_competition_positions(open_only=False)
        else:
            items = store.list_competition_positions(open_only=False, status=status)
    elif status == "open":
        items = store.list_positions(portfolio.id, open_only=True)
    elif status == "all":
        items = store.list_positions(portfolio.id, open_only=False)
    else:
        items = store.list_positions(portfolio.id, open_only=False, status=status)

    from quantara_engine.persistence.batch_summary import batch_instruments_by_id

    instrument_ids = [p.instrument_id for p in items if p.instrument_id]
    instruments = batch_instruments_by_id(store, instrument_ids)
    strategy_labels = _batch_strategy_labels_from_instances(
        store, [p.strategy_instance_id for p in items if p.strategy_instance_id]
    )

    result = []
    for p in items:
        inst = instruments.get(p.instrument_id) if p.instrument_id else None
        cp = float(p.current_price or p.entry_price)
        upnl_pct = (
            float(p.unrealized_pnl / (p.entry_price * p.quantity) * 100)
            if p.quantity
            else 0
        )
        strat_name, strat_version = strategy_labels.get(
            p.strategy_instance_id, ("Gold Trend Pullback", "1.0.0")
        )
        result.append({
            "id": p.id,
            "instrument": inst.symbol if inst else "XAUUSD",
            "direction": p.direction.value.upper(),
            "size": float(p.quantity),
            "entry_price": float(p.entry_price),
            "entry_time": p.opened_at.isoformat() if p.opened_at else "",
            "current_price": cp,
            "stop_loss": float(p.stop_loss) if p.stop_loss else None,
            "take_profit": float(p.take_profit) if p.take_profit else None,
            "unrealized_pnl": float(p.unrealized_pnl),
            "unrealized_pnl_pct": upnl_pct,
            "strategy_name": strat_name,
            "strategy_version": strat_version,
        })
    return result


@router.get("/trades")
def trades(store: StoreDep, portfolio_id: str = "competition"):
    portfolio = _resolve_portfolio(store, portfolio_id)
    if _is_combined_competition_portfolio(portfolio.id) or portfolio_id in (
        "competition",
        "paper-main",
        "paper",
        "combined",
    ):
        rows = store.list_competition_trades_all(limit=500)
    else:
        rows = store.list_trades(portfolio.id, limit=500, paper_only=True)
    result = []
    for t in rows:
        inst = store.get_instrument_by_symbol("XAUUSD")
        if t.instrument_id:
            from quantara_engine.models.instruments import Instrument as OrmInstrument

            row = store.session.get(OrmInstrument, uuid.UUID(t.instrument_id))
            if row:
                inst = store._instrument_to_domain(row)
        strat_name, strat_version = _strategy_label(store, t.strategy_version_id)
        result.append({
            "id": t.id,
            "close_time": t.closed_at.isoformat() if t.closed_at else "",
            "instrument": inst.symbol if inst else "XAUUSD",
            "direction": t.direction.value.upper(),
            "entry_price": float(t.entry_price),
            "exit_price": float(t.exit_price),
            "pnl": float(t.realized_pnl),
            "duration": str(t.duration_seconds) + "s",
            "exit_reason": t.exit_reason.value,
            "strategy_name": strat_name,
            "strategy_version": strat_version,
            "fees": float(t.fees_total),
        })
    return result


@router.get("/competition")
def competition_summary(store: StoreDep):
    return build_competition_response(store)


@router.get("/competition/equity-curves")
def competition_equity_curves(store: StoreDep, limit: int = 100):
    return {"equity_curves": build_competition_equity_curves(store, limit_per_portfolio=limit)}


@router.get("/analytics/risk-concentration")
def analytics_risk_concentration(store: StoreDep):
    from quantara_engine.broker.state_builder import build_competition_broker_account
    from quantara_engine.risk.concentration import build_physical_concentration

    account = build_competition_broker_account(store)
    broker_equity = account.equity if account else Decimal("320000")
    return build_physical_concentration(store, broker_equity=broker_equity)


@router.get("/analytics/regime-performance")
def analytics_regime_performance(store: StoreDep):
    from quantara_engine.analytics.regime_performance import build_regime_performance

    return build_regime_performance(store)


@router.get("/portfolios")
def portfolios_list(store: StoreDep):
    from quantara_engine.competition.multi_strategy_constants import PORTFOLIO_DEF_BY_ID as MULTI_DEFS
    from quantara_engine.competition.orb_constants import ORB_PORTFOLIO_DEF_BY_ID
    from quantara_engine.strategies.registry import get as get_strategy

    _, _, all_entries = store.list_all_competition_entries()
    portfolio_ids = [e["portfolio"].id for e in all_entries]
    batch_stats = store.batch_portfolio_dashboard_stats(portfolio_ids)

    items = []
    for entry in all_entries:
        p = entry["portfolio"]
        slug = entry["instance"].strategy_slug
        version = entry["instance"].strategy_version
        portfolio_def = PORTFOLIO_DEF_BY_ID.get(p.id)
        orb_def = ORB_PORTFOLIO_DEF_BY_ID.get(p.id)
        multi_def = MULTI_DEFS.get(p.id)
        display_name = (
            portfolio_def.name_he
            if portfolio_def
            else orb_def.name_he
            if orb_def
            else multi_def.name_he
            if multi_def
            else p.name
        )
        try:
            strategy_name = get_strategy(slug, version).name()
        except KeyError:
            strategy_name = slug
        stats = batch_stats.get(p.id, {})
        open_positions = stats.get("open_positions") or []
        open_pos = open_positions[0] if open_positions else None
        items.append(
            {
                "id": p.id,
                "name": display_name,
                "kind": "competition",
                "robot_label": ROBOT_LABELS.get(slug, slug),
                "strategy_slug": slug,
                "strategy_name": strategy_name,
                "timeframe": entry["instance"].timeframe,
                "timeframe_he": TIMEFRAME_HE.get(entry["instance"].timeframe, entry["instance"].timeframe),
                "risk_slug": entry["risk_profile"].slug,
                "risk_per_trade_pct": float(entry["risk_profile"].risk_per_trade_pct),
                "initial_capital": float(p.initial_capital),
                "balance": float(p.balance),
                "equity": float(p.equity),
                "realized_pnl": float(stats.get("realized_pnl", 0)),
                "unrealized_pnl": float(p.unrealized_pnl),
                "open_positions_count": stats.get("open_positions_count", 0),
                "open_position": stats.get("open_positions_count", 0) > 0,
                "open_direction": open_pos.direction.value if open_pos else None,
                "closed_trades_count": stats.get("closed_trades_count", 0),
                "sort_order": entry["sort_order"],
            }
        )
    items.sort(key=lambda row: (row["robot_label"], row.get("sort_order", 99)))
    return items


@router.get("/decisions")
def decisions(
    store: StoreDep,
    limit: int = 50,
    portfolio_id: str | None = None,
):
    symbol_map = store.resolve_instrument_display_symbols()
    if portfolio_id:
        items = store.list_decisions_for_portfolio(portfolio_id, limit=limit)
    else:
        competition = store.list_competition_instance_ids()
        if competition:
            items = store.list_competition_decisions(limit=limit)
        else:
            items = store.list_decisions(limit=limit)
    return [_decision_payload(d, symbol_map) for d in items]


def _decision_payload(d, symbol_map: dict[str, str]) -> dict:
    signal = d.metadata.get("signal") if d.metadata else None
    instrument = symbol_map.get(d.instrument_id, d.instrument_id)
    return {
        "id": d.id,
        "timestamp": d.candle_timestamp.isoformat(),
        "decision_type": d.decision_type.value,
        "message": d.message,
        "instrument": instrument,
        "instrument_id": d.instrument_id,
        "candle_time": d.candle_timestamp.isoformat(),
        "strategy_instance_id": d.strategy_instance_id,
        "signal": signal,
    }


@router.get("/decisions/latest")
def decisions_latest(store: StoreDep):
    d = store.get_latest_decision()
    if not d:
        return {
            "id": "none",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "decision_type": "no_setup",
            "message": "NO_SETUP — awaiting strategy evaluation",
            "signal": None,
        }
    symbol_map = store.resolve_instrument_display_symbols()
    return _decision_payload(d, symbol_map)


@router.get("/decisions/by-asset")
def decisions_by_asset(
    store: StoreDep,
    timeframe: str = "5m",
    include_freshness: bool = True,
):
    symbol_map = store.resolve_instrument_display_symbols()
    identity_map = store.build_instance_strategy_identity_map()
    items = store.list_latest_decisions_by_asset_timeframe(timeframe)
    now = datetime.now(timezone.utc)
    open_instruments = {
        pos.instrument_id for pos in store.list_competition_positions(open_only=True)
    }
    rows = []
    for d in items:
        payload = _decision_payload(d, symbol_map)
        identity = identity_map.get(d.strategy_instance_id, {})
        payload["robot_label"] = identity.get("robot_label")
        payload["strategy_slug"] = identity.get("strategy_slug")
        payload["strategy_name"] = identity.get("strategy_name")
        last_ts = d.candle_timestamp
        age_min = (now - last_ts).total_seconds() / 60
        payload["fresh"] = age_min < 30
        payload["candle_age_minutes"] = round(age_min, 1)
        payload["timeframe"] = timeframe
        entry_signal = d.decision_type.value in ("buy_signal", "sell_signal")
        payload["entry_signal"] = entry_signal
        payload["trade_opened"] = entry_signal
        payload["position_open"] = d.instrument_id in open_instruments
        rows.append(payload)
    rows.sort(key=lambda row: (row.get("robot_label") or "", row.get("instrument") or ""))
    payload: dict = {
        "timeframe": timeframe,
        "decisions": rows,
    }
    if include_freshness:
        from quantara_workers.jobs.run_strategy import strategy_freshness_summary

        payload["strategy_freshness"] = strategy_freshness_summary(store, now)
    return payload


from quantara_engine.competition.robot_registry import ROBOT_LABELS  # noqa: E402


@router.get("/strategies")
def strategies_list(store: StoreDep):
    grouped: dict = {}
    for item in list_all():
        slug = item["slug"]
        if slug not in grouped:
            grouped[slug] = {
                "id": slug,
                "slug": slug,
                "name": item["name"],
                "robot_label": ROBOT_LABELS.get(slug),
                "status": "active",
                "versions_count": 0,
                "instruments": item.get("supported_instruments", []),
                "timeframes": item.get("supported_timeframes", []),
                "active_instances": store.count_active_instances(slug),
            }
        grouped[slug]["versions_count"] += 1
    return list(grouped.values())


@router.get("/strategies/opening-range-breakout/status")
def orb_strategy_status(store: StoreDep, symbol: str | None = None):
    return build_orb_status(store, symbol=symbol)


@router.get("/strategies/{slug}/versions")
def strategy_versions(store: StoreDep, slug: str):
    result = []
    for item in list_all():
        if item["slug"] != slug:
            continue
        cls = get(slug, item["version"])
        version_row = store.get_strategy_version_by_slug(slug, item["version"])
        version_id = version_row["id"] if version_row else f"{slug}-{item['version']}"
        trades_count = store.count_trades_for_version(version_id) if version_row else 0
        backtests_count = store.count_backtests_for_version(version_id) if version_row else 0
        result.append({
            "id": version_id,
            "version": item["version"],
            "status": "active",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "parameters": item.get("default_parameters"),
            "trades_count": trades_count,
            "backtests_count": backtests_count,
            "logic_hash": (
                version_row.get("logic_hash", cls.version() + "-hash")
                if version_row
                else cls.version() + "-hash"
            ),
        })
    if not result:
        raise HTTPException(404, "Strategy not found")
    return result


@router.get("/backtests")
def backtests_list(store: StoreDep):
    rows = store.list_backtests()
    result = []
    for bt in rows:
        strat_name, strat_version = _strategy_label(store, bt["strategy_version_id"])
        metrics = bt.get("metrics") or {}
        result.append({
            "id": bt["id"],
            "strategy_name": strat_name,
            "strategy_version": strat_version,
            "period_start": bt.get("start_date") or "",
            "period_end": bt.get("end_date") or "",
            "status": bt.get("status"),
            "return_pct": metrics.get("total_return_pct"),
            "win_rate": metrics.get("win_rate"),
            "max_drawdown_pct": metrics.get("maximum_drawdown_pct"),
            "trades_count": metrics.get("total_trades", 0),
            "created_at": bt.get("started_at") or datetime.now(timezone.utc).isoformat(),
            "profit_factor": metrics.get("profit_factor"),
            "dataset_fingerprint": bt.get("dataset_fingerprint"),
        })
    return result


class BacktestRequest(BaseModel):
    strategy_instance_id: str = "si-gold-1h"
    candle_count: int = 250
    initial_capital: float = 10000.0
    timeframe: str = "1h"


@router.post("/backtests")
def run_backtest(store: StoreDep, req: BacktestRequest):
    entries = store.list_competition_entries()
    if not entries:
        raise HTTPException(404, "No active competition portfolios — run seed_competition first")
    instance = entries[0]["instance"]
    portfolio = entries[0]["portfolio"]

    instrument = store.get_instrument_by_symbol("XAUUSD")
    if not instrument:
        raise HTTPException(404, "Instrument not found")

    risk_profile = store.get_risk_profile_by_slug("balanced")
    if not risk_profile:
        raise HTTPException(404, "Risk profile not found")

    candles = store.list_candles(instrument.id, req.timeframe, limit=req.candle_count)
    if len(candles) < req.candle_count:
        if settings.market_data_provider == "mock":
            provider = get_market_data_provider("mock")
            generated = provider.generate_candles(
                instrument.id, req.timeframe, req.candle_count
            )
            for candle in generated:
                store.upsert_candle(candle)
            candles = store.list_candles(
                instrument.id, req.timeframe, limit=req.candle_count
            )
        else:
            raise HTTPException(
                400,
                f"Insufficient stored candles ({len(candles)}/{req.candle_count}). "
                "Run scripts/import_historical.py or wait for data ingestion.",
            )

    if not candles:
        raise HTTPException(400, "No candle data available for backtest")

    run_id = str(uuid.uuid4())
    bt = BacktestRun(
        id=run_id,
        strategy_instance=instance,
        instrument=instrument,
        risk_profile=risk_profile,
        candles=candles,
        initial_capital=Decimal(str(req.initial_capital)),
        execution_assumptions=ExecutionAssumptions(),
    )
    runner = BacktestRunner()
    bt = runner.run(bt, store=store)
    return {"id": run_id, "status": bt.status.value}


@router.get("/backtests/{backtest_id}")
def backtest_detail(store: StoreDep, backtest_id: str):
    bt = store.get_backtest(backtest_id)
    if not bt:
        raise HTTPException(404, "Backtest not found")
    m = bt.get("metrics") or {}
    strat_name, strat_version = _strategy_label(store, bt["strategy_version_id"])
    version = store.resolve_strategy_version_ref(bt["strategy_version_id"])
    strategy_slug = version.get("strategy_slug", "") if version else ""
    return {
        "id": bt["id"],
        "status": bt.get("status"),
        "strategy_name": strat_name,
        "strategy_slug": strategy_slug,
        "strategy_version": strat_version,
        "period_start": bt.get("start_date") or "",
        "period_end": bt.get("end_date") or "",
        "return_pct": m.get("total_return_pct"),
        "win_rate": m.get("win_rate"),
        "max_drawdown_pct": m.get("maximum_drawdown_pct"),
        "trades_count": m.get("total_trades", 0),
        "profit_factor": m.get("profit_factor"),
        "parameters": bt.get("parameters"),
        "execution_assumptions": bt.get("execution_assumptions"),
        "dataset_fingerprint": bt.get("dataset_fingerprint"),
        "metrics": m,
        "error_message": bt.get("error_message"),
        "created_at": bt.get("started_at") or datetime.now(timezone.utc).isoformat(),
    }


@router.get("/backtests/{backtest_id}/trades")
def backtest_trades(store: StoreDep, backtest_id: str):
    bt = store.get_backtest(backtest_id)
    if not bt:
        raise HTTPException(404, "Backtest not found")
    rows = store.list_backtest_trades(backtest_id)
    return [
        {
            "id": t.id,
            "close_time": t.closed_at.isoformat() if t.closed_at else "",
            "instrument": "XAUUSD",
            "direction": t.direction.value.upper(),
            "entry_price": float(t.entry_price),
            "exit_price": float(t.exit_price),
            "pnl": float(t.realized_pnl),
            "exit_reason": t.exit_reason.value,
        }
        for t in rows
    ]


@router.get("/experiments")
def experiments_list(store: StoreDep):
    return [
        {
            "id": e["id"],
            "name": e["name"],
            "status": e["status"],
            "instances_count": 0,
            "period_start": e.get("start_date"),
            "period_end": e.get("end_date"),
        }
        for e in store.list_experiments()
    ]


@router.get("/analytics/portfolio")
def analytics_portfolio(store: StoreDep, portfolio_id: str = "competition"):
    portfolio = _resolve_portfolio(store, portfolio_id)
    if _is_combined_competition_portfolio(portfolio.id):
        payload = build_competition_response(store)
        if not payload.get("active"):
            raise HTTPException(404, "Competition not configured")
        return {
            "equity_curve": [],
            "drawdown_curve": [],
            "daily_returns": [],
            "monthly_returns": [],
            "summary": {
                "total_return_pct": round(
                    float(payload["combined"]["combined_pnl"])
                    / float(payload["experiment"]["total_initial_capital"])
                    * 100,
                    2,
                )
                if payload["experiment"]["total_initial_capital"]
                else 0,
                "max_drawdown_pct": 0,
                "win_rate": None,
            },
        }
    state = store.load_portfolio_state(portfolio.id)
    svc = AnalyticsService()
    metrics = svc.portfolio_analytics(
        portfolio.initial_capital,
        portfolio.equity,
        state.trades,
        state.snapshots,
    )
    curve = svc.equity_curve(state.snapshots) or [
        {"date": datetime.now(timezone.utc).isoformat(), "equity": float(portfolio.equity)}
    ]
    dd = svc.drawdown(state.snapshots) or [
        {"date": datetime.now(timezone.utc).isoformat(), "drawdown_pct": 0}
    ]
    return {
        "equity_curve": [
            {"date": c.get("timestamp", c.get("date", "")), "equity": c["equity"]}
            for c in curve
        ],
        "drawdown_curve": [
            {"date": d.get("timestamp", d.get("date", "")), "drawdown_pct": d.get("drawdown_pct", 0)}
            for d in dd
        ],
        "daily_returns": [],
        "monthly_returns": [],
        "summary": {
            "total_return_pct": metrics.get("total_return_pct", 0),
            "max_drawdown_pct": metrics.get("maximum_drawdown_pct", 0),
            "win_rate": metrics.get("win_rate"),
        },
    }


def _analytics_for_strategy_ref(store: TradingStore, strategy_version_id: str) -> dict:
    version = store.resolve_strategy_version_ref(strategy_version_id)
    if not version:
        raise HTTPException(404, "Strategy version not found")

    trades = store.list_competition_trades_all(limit=1000)
    trades = [t for t in trades if t.strategy_version_id == version["id"]]
    svc = AnalyticsService()
    stats = svc.strategy_analytics(trades, version["id"])
    return {
        "strategy_slug": version.get("strategy_slug", ""),
        "strategy_name": version.get("strategy_name", "Unknown"),
        "strategy_version": version.get("version", "1.0.0"),
        "robot_label": ROBOT_LABELS.get(version.get("strategy_slug", ""), ""),
        "trade_count": len(trades),
        "win_rate": stats.get("win_rate", 0),
        "profit_factor": stats.get("profit_factor", 0),
        "expectancy": stats.get("expectancy", 0),
        "long_pnl": stats.get("long_pnl", 0),
        "short_pnl": stats.get("short_pnl", 0),
    }


@router.get("/analytics/strategy")
def analytics_strategy(store: StoreDep, strategy_version_id: str = "gtp-v1"):
    return _analytics_for_strategy_ref(store, strategy_version_id)


@router.get("/analytics/strategy-breakdown")
def analytics_strategy_breakdown(store: StoreDep):
    """Mandatory Robot A vs Robot B performance split."""
    breakdown = []
    for ref in ("gtp-v1", "orb-v1"):
        version = store.resolve_strategy_version_ref(ref)
        if not version:
            breakdown.append(
                {
                    "strategy_slug": "opening-range-breakout" if ref == "orb-v1" else "gold-trend-pullback",
                    "strategy_name": "Opening Range Breakout" if ref == "orb-v1" else "Gold Trend Pullback",
                    "strategy_version": "1.0.0",
                    "robot_label": ROBOT_LABELS.get(
                        "opening-range-breakout" if ref == "orb-v1" else "gold-trend-pullback"
                    ),
                    "trade_count": 0,
                    "win_rate": 0,
                    "profit_factor": 0,
                    "expectancy": 0,
                    "long_pnl": 0,
                    "short_pnl": 0,
                    "configured": False,
                }
            )
            continue
        payload = _analytics_for_strategy_ref(store, ref)
        payload["configured"] = True
        breakdown.append(payload)
    return {"strategies": breakdown}


@router.get("/analytics/costs")
def analytics_costs(store: StoreDep, portfolio_id: str = "competition"):
    portfolio = _resolve_portfolio(store, portfolio_id)
    if _is_combined_competition_portfolio(portfolio.id):
        rows = store.list_competition_trades_all(limit=1000)
    else:
        rows = store.list_trades(portfolio.id, limit=1000, paper_only=True)
    fees = sum((t.fees_total for t in rows), Decimal("0"))
    slip = sum((t.slippage_total for t in rows), Decimal("0"))
    spread = sum((t.spread_total for t in rows), Decimal("0"))
    return {
        "total_fees": float(fees),
        "total_slippage": float(slip),
        "spread_impact": float(spread),
    }


@router.get("/analytics/competition")
def analytics_competition(store: StoreDep):
    payload = build_competition_response(store)
    if not payload.get("active"):
        raise HTTPException(404, "Competition not configured")
    return {
        "equity_curves": payload["equity_curves"],
        "portfolios": payload["portfolios"],
        "leaderboard": payload["leaderboard"],
        "combined": payload["combined"],
        "experiment": payload["experiment"],
        "robot_groups": payload.get("robot_groups", []),
    }


@router.get("/analytics/today")
def analytics_today(store: StoreDep, portfolio_id: str = "competition"):
    """Today's paper-trading activity for the Home dashboard (not backtest-wide)."""
    from quantara_engine.api.dashboard_cache import cached_home_payload

    if store.list_competition_entries():
        return cached_home_payload("analytics_today", lambda: _analytics_today_payload(store))
    raise HTTPException(404, "Competition not configured")


def _analytics_today_payload(store: StoreDep):
    stats = store.get_competition_today_stats()
    entries = store.list_competition_entries()
    portfolios = [e["portfolio"] for e in entries]
    combined_equity = sum(float(p.equity) for p in portfolios)
    summaries = [
        {
            "id": e["portfolio"].id,
            "name": PORTFOLIO_DEF_BY_ID.get(e["portfolio"].id).name_he
            if PORTFOLIO_DEF_BY_ID.get(e["portfolio"].id)
            else e["portfolio"].name,
            "return_pct": round(
                float(
                    (e["portfolio"].equity - e["portfolio"].initial_capital)
                    / e["portfolio"].initial_capital
                    * 100
                )
                if e["portfolio"].initial_capital > 0
                else 0,
                2,
            ),
            "timeframe": e["instance"].timeframe,
            "timeframe_he": TIMEFRAME_HE.get(e["instance"].timeframe, e["instance"].timeframe),
        }
        for e in entries
    ]
    leader_row = max(summaries, key=lambda row: row["return_pct"], default=None)
    tf_groups: dict[str, list[float]] = {}
    for row in summaries:
        tf_groups.setdefault(row["timeframe"], []).append(row["return_pct"])
    leading_timeframe = None
    if tf_groups:
        best_tf = max(tf_groups.items(), key=lambda item: sum(item[1]) / len(item[1]))
        leading_timeframe = {
            "timeframe": best_tf[0],
            "timeframe_he": TIMEFRAME_HE.get(best_tf[0], best_tf[0]),
            "title_he": TIMEFRAME_GROUP_TITLE_HE.get(best_tf[0], best_tf[0]),
            "average_return_pct": round(sum(best_tf[1]) / len(best_tf[1]), 2),
        }
    open_positions_total = store.count_open_competition_positions()
    return {
        "scope": "competition",
        "portfolio_count": len(entries),
        **stats,
        "combined_equity": round(combined_equity, 2),
        "open_positions_total": open_positions_total,
        "leader": leader_row,
        "leading_timeframe": leading_timeframe,
    }


@router.get("/workers/status")
def workers_status(store: StoreDep):
    from quantara_workers.jobs.run_strategy import strategy_freshness_summary

    db_runs = store.latest_worker_runs()
    cache = runtime_cache.worker_status
    workers = []
    strategy_freshness = strategy_freshness_summary(store)
    for name in ("data_fetcher", "strategy_runner", "backtest_runner"):
        db_run = db_runs.get(name)
        cached = cache.get(name, {})
        last_run = None
        if db_run and db_run.completed_at:
            last_run = db_run.completed_at.isoformat()
        elif cached.get("last_run"):
            last_run = cached["last_run"]
        status_val = cached.get("status", "idle")
        if db_run and name != "strategy_runner":
            status_val = "healthy" if db_run.status.value == "success" else db_run.status.value
        worker_payload = {
            "name": name,
            "status": "running" if status_val in ("healthy", "running", "idle", "catching_up") else "stopped",
            "last_run": last_run,
        }
        if name == "strategy_runner":
            worker_payload["execution_status"] = cached.get("status", status_val)
            worker_payload["timeframes"] = cached.get("timeframes", {})
            worker_payload["backlog"] = cached.get("jobs_pending", strategy_freshness.get("backlog", 0))
            worker_payload["freshness"] = strategy_freshness
        workers.append(worker_payload)

    healthy = strategy_freshness.get("healthy", False) and all(
        w["status"] == "running"
        for w in workers
        if w["name"] in ("data_fetcher", "backtest_runner")
    )
    return {
        "healthy": healthy,
        "workers": workers,
        "strategy_freshness": strategy_freshness,
    }


@router.post("/paper/start")
def paper_start(store: StoreDep):
    store.update_settings("paper_trading_enabled", True)
    store.set_competition_portfolio_status(PortfolioStatus.ACTIVE)
    from quantara_engine.trading.trading_controls import TradingControlState, transition_to

    transition_to(store, TradingControlState.RUNNING)
    return {"status": "active"}


@router.post("/paper/stop")
def paper_stop(store: StoreDep):
    store.update_settings("paper_trading_enabled", False)
    store.set_competition_portfolio_status(PortfolioStatus.HALTED)
    from quantara_engine.trading.trading_controls import TradingControlState, transition_to

    transition_to(store, TradingControlState.PAUSE_TRADING)
    return {"status": "halted"}


@router.get("/trading-control")
def get_trading_control(store: StoreDep):
    from quantara_engine.trading.trading_controls import TradingControlState, load_trading_control

    snap = load_trading_control(store.get_settings_dict())
    open_count = store.count_open_competition_positions()
    progress = None
    if snap.state == TradingControlState.FLATTENING and snap.open_positions_at_flatten > 0:
        done = max(0, snap.open_positions_at_flatten - open_count)
        progress = round(done / snap.open_positions_at_flatten * 100, 1)
    return {
        **snap.to_dict(),
        "open_positions_remaining": open_count,
        "flatten_progress_pct": progress,
        "assets_awaiting_market_reopen": list(snap.pending_market_reopen),
    }


@router.post("/trading-control/{action}")
def post_trading_control(action: str, store: StoreDep):
    from quantara_engine.trading.trading_controls import TradingControlState, load_trading_control, transition_to

    open_count = store.count_open_competition_positions()
    action = action.replace("-", "_").lower()
    if action in ("running", "resume", "resume_trading"):
        snap = transition_to(store, TradingControlState.RUNNING)
    elif action in ("pause_new_entries", "pause_entries"):
        snap = transition_to(store, TradingControlState.PAUSE_NEW_ENTRIES)
    elif action in ("pause_trading", "pause"):
        snap = transition_to(store, TradingControlState.PAUSE_TRADING)
    elif action in ("flatten", "stop_and_close_all", "flatten_all"):
        snap = transition_to(
            store,
            TradingControlState.FLATTENING,
            open_positions=open_count,
        )
    elif action in ("stopped", "stop"):
        snap = transition_to(store, TradingControlState.STOPPED)
    else:
        from fastapi import HTTPException

        raise HTTPException(status_code=400, detail=f"Unknown action: {action}")
    return {
        **snap.to_dict(),
        "open_positions_remaining": store.count_open_competition_positions(),
    }


@router.get("/settings")
def get_settings(store: StoreDep):
    return store.settings_api_response()


@router.put("/settings")
def update_settings(store: StoreDep, payload: dict):
    store.update_settings_bulk(payload)
    return store.settings_api_response()
