"""API routes — Engine REST API v1 (PostgreSQL via TradingStore)."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from quantara_engine.analytics.service import AnalyticsService
from quantara_engine.api.deps import get_store, verify_api_key
from quantara_engine.api.state import runtime_cache
from quantara_engine.backtesting.runner import BacktestRun, BacktestRunner
from quantara_engine.core.config import settings
from quantara_engine.domain.types import ExecutionAssumptions, Mode, PortfolioStatus
from quantara_engine.market_data.factory import get_market_data_provider
from quantara_engine.persistence.store import TradingStore
from quantara_engine.strategies.registry import get, list_all

router = APIRouter(prefix="/api/v1", dependencies=[Depends(verify_api_key)])

StoreDep = Annotated[TradingStore, Depends(get_store)]


def _drawdown_pct(equity: Decimal, peak_equity: Decimal) -> float:
    if peak_equity <= 0:
        return 0.0
    return float((peak_equity - equity) / peak_equity * 100)


def _portfolio_ui(store: TradingStore, portfolio_ref: str = "paper-main") -> dict:
    portfolio = store.resolve_paper_portfolio(portfolio_ref)
    settings = store.get_settings_dict()
    realized = store.sum_realized_pnl(portfolio.id)
    daily = Decimal("0")
    snaps = store.list_snapshots(portfolio.id, limit=30)
    if snaps:
        today = datetime.now(timezone.utc).date()
        today_snaps = [s for s in snaps if s.timestamp.date() == today]
        if len(today_snaps) >= 2:
            daily = today_snaps[-1].equity - today_snaps[0].equity

    return {
        "equity": float(portfolio.equity),
        "cash_balance": float(portfolio.balance),
        "unrealized_pnl": float(portfolio.unrealized_pnl),
        "realized_pnl": float(realized),
        "daily_pnl": float(daily),
        "peak_equity": float(portfolio.peak_equity),
        "current_drawdown_pct": _drawdown_pct(portfolio.equity, portfolio.peak_equity),
        "risk_profile": settings.get("default_risk_profile", "balanced"),
        "mode": portfolio.mode.value,
    }


def _strategy_label(store: TradingStore, strategy_version_id: str) -> tuple[str, str]:
    version = store.resolve_strategy_version_ref(strategy_version_id)
    if version:
        name = version.get("strategy_name") or version.get("strategy_slug", "").replace("-", " ").title()
        return name or "Gold Trend Pullback", version.get("version", "1.0.0")
    return "Gold Trend Pullback", "1.0.0"


def _strategy_label_from_instance(store: TradingStore, strategy_instance_id: str) -> tuple[str, str]:
    from quantara_engine.models.portfolio import StrategyInstance as OrmStrategyInstance

    row = store.session.get(OrmStrategyInstance, uuid.UUID(strategy_instance_id))
    if row:
        return _strategy_label(store, str(row.strategy_version_id))
    return "Gold Trend Pullback", "1.0.0"


@router.get("/health")
def health():
    return {"status": "ok", "service": "quantara-engine"}


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

    rows = store.list_candles(inst.id, timeframe, limit=50)
    if len(rows) < 2 and settings.market_data_provider == "mock":
        provider = get_market_data_provider("mock")
        generated = provider.generate_candles(inst.id, timeframe, 50)
        for candle in generated:
            store.upsert_candle(candle)
        rows = store.list_candles(inst.id, timeframe, limit=50)
    if len(rows) < 1:
        raise HTTPException(404, "No candle data available")

    last = rows[-1]
    if len(rows) >= 2:
        prev = rows[-2]
        change = float(last.close - prev.close)
        change_pct = float(change / prev.close * 100) if prev.close else 0
    else:
        change = 0.0
        change_pct = 0.0
    return {
        "instrument": instrument,
        "price": float(last.close),
        "change": change,
        "change_pct": change_pct,
        "last_update": last.timestamp.isoformat(),
        "timeframe": timeframe,
    }


@router.get("/market-data/status")
def market_data_status(store: StoreDep):
    inst = store.get_instrument_by_symbol("XAUUSD")
    provider = settings.market_data_provider
    last_fetch = None
    stale = True
    counts: dict[str, int] = {}
    if inst:
        for tf in ("5m", "15m", "1h"):
            counts[tf] = store.count_candles(inst.id, tf)
        last_fetch = store.latest_candle_timestamp(inst.id, "1h")
        if last_fetch:
            age_h = (datetime.now(timezone.utc) - last_fetch).total_seconds() / 3600
            stale = age_h > 2 if provider == "twelvedata" else age_h > 24
    return {
        "healthy": not stale and (sum(counts.values()) > 0 if provider == "twelvedata" else True),
        "provider": provider,
        "source": provider,
        "last_fetch": last_fetch.isoformat() if last_fetch else None,
        "candle_counts": counts,
        "gaps": 0,
        "stale": stale,
    }


@router.get("/portfolio")
def portfolio(store: StoreDep, portfolio_id: str = "paper-main"):
    return _portfolio_ui(store, portfolio_id)


@router.get("/portfolio/risk-status")
def portfolio_risk_status(store: StoreDep, portfolio_id: str = "paper-main"):
    portfolio = store.resolve_paper_portfolio(portfolio_id)
    settings = store.get_settings_dict()
    exposure_pct = (
        float(portfolio.exposure_notional / portfolio.equity * 100)
        if portfolio.equity > 0
        else 0
    )
    return {
        "profile": settings.get("default_risk_profile", "balanced"),
        "halted": portfolio.status == PortfolioStatus.HALTED,
        "exposure_pct": round(exposure_pct, 2),
        "halt_reason": portfolio.halt_reason,
    }


@router.get("/portfolio/snapshots")
def portfolio_snapshots(
    store: StoreDep,
    portfolio_id: str = "paper-main",
    limit: int = 30,
):
    portfolio = store.resolve_paper_portfolio(portfolio_id)
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
    portfolio_id: str = "paper-main",
    status: str = "open",
):
    portfolio = store.resolve_paper_portfolio(portfolio_id)
    if status == "open":
        items = store.list_positions(portfolio.id, open_only=True)
    elif status == "all":
        items = store.list_positions(portfolio.id, open_only=False)
    else:
        items = store.list_positions(portfolio.id, open_only=False, status=status)

    result = []
    for p in items:
        inst = None
        if p.instrument_id:
            from quantara_engine.models.instruments import Instrument as OrmInstrument

            row = store.session.get(OrmInstrument, uuid.UUID(p.instrument_id))
            if row:
                inst = store._instrument_to_domain(row)
        cp = float(p.current_price or p.entry_price)
        upnl_pct = (
            float(p.unrealized_pnl / (p.entry_price * p.quantity) * 100)
            if p.quantity
            else 0
        )
        strat_name, strat_version = _strategy_label_from_instance(store, p.strategy_instance_id)
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
def trades(store: StoreDep, portfolio_id: str = "paper-main"):
    portfolio = store.resolve_paper_portfolio(portfolio_id)
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


@router.get("/decisions")
def decisions(store: StoreDep, limit: int = 50):
    items = store.list_decisions(limit=limit)
    return [
        {
            "id": d.id,
            "timestamp": d.candle_timestamp.isoformat(),
            "decision_type": d.decision_type.value,
            "message": d.message,
            "instrument": "XAUUSD",
            "candle_time": d.candle_timestamp.isoformat(),
        }
        for d in items
    ]


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
    signal = d.metadata.get("signal") if d.metadata else None
    return {
        "id": d.id,
        "timestamp": d.candle_timestamp.isoformat(),
        "decision_type": d.decision_type.value,
        "message": d.message,
        "signal": signal,
    }


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
                "status": "active",
                "versions_count": 0,
                "instruments": item.get("supported_instruments", []),
                "timeframes": item.get("supported_timeframes", []),
                "active_instances": store.count_active_instances(slug),
            }
        grouped[slug]["versions_count"] += 1
    return list(grouped.values())


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
    portfolio = store.resolve_paper_portfolio()
    instance = store.get_paper_strategy_instance(portfolio.id)
    if not instance:
        raise HTTPException(404, "Strategy instance not found — run seed and create instance")

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
    return {
        "id": bt["id"],
        "status": bt.get("status"),
        "strategy_name": strat_name,
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
def analytics_portfolio(store: StoreDep, portfolio_id: str = "paper-main"):
    portfolio = store.resolve_paper_portfolio(portfolio_id)
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


@router.get("/analytics/strategy")
def analytics_strategy(store: StoreDep, strategy_version_id: str = "gtp-v1"):
    version = store.resolve_strategy_version_ref(strategy_version_id)
    if not version:
        raise HTTPException(404, "Strategy version not found")

    portfolio = store.resolve_paper_portfolio()
    trades = [
        t for t in store.list_trades(portfolio.id, limit=1000, paper_only=True)
        if t.strategy_version_id == version["id"]
    ]
    svc = AnalyticsService()
    stats = svc.strategy_analytics(trades, version["id"])
    return {
        "strategy_name": version.get("strategy_name", "Gold Trend Pullback"),
        "strategy_version": version.get("version", "1.0.0"),
        "win_rate": stats.get("win_rate", 0),
        "profit_factor": stats.get("profit_factor", 0),
        "expectancy": stats.get("expectancy", 0),
        "long_pnl": stats.get("long_pnl", 0),
        "short_pnl": stats.get("short_pnl", 0),
    }


@router.get("/analytics/costs")
def analytics_costs(store: StoreDep, portfolio_id: str = "paper-main"):
    portfolio = store.resolve_paper_portfolio(portfolio_id)
    rows = store.list_trades(portfolio.id, limit=1000, paper_only=True)
    fees = sum((t.fees_total for t in rows), Decimal("0"))
    slip = sum((t.slippage_total for t in rows), Decimal("0"))
    spread = sum((t.spread_total for t in rows), Decimal("0"))
    return {
        "total_fees": float(fees),
        "total_slippage": float(slip),
        "spread_impact": float(spread),
    }


@router.get("/analytics/today")
def analytics_today(store: StoreDep, portfolio_id: str = "paper-main"):
    """Today's paper-trading activity for the Home dashboard (not backtest-wide)."""
    portfolio = store.resolve_paper_portfolio(portfolio_id)
    instance = store.get_paper_strategy_instance(portfolio.id)
    return {
        "scope": "paper",
        "portfolio_id": portfolio.id,
        "strategy_instance_id": instance.id if instance else None,
        "trades_count": store.count_trades_today(portfolio.id, paper_only=True),
        "decisions_count": store.count_decisions_today(
            strategy_instance_id=instance.id if instance else None,
            mode=Mode.PAPER,
        ),
    }


@router.get("/workers/status")
def workers_status(store: StoreDep):
    db_runs = store.latest_worker_runs()
    cache = runtime_cache.worker_status
    workers = []
    for name in ("data_fetcher", "strategy_runner", "backtest_runner"):
        db_run = db_runs.get(name)
        cached = cache.get(name, {})
        last_run = None
        if db_run and db_run.completed_at:
            last_run = db_run.completed_at.isoformat()
        elif cached.get("last_run"):
            last_run = cached["last_run"]
        status_val = cached.get("status", "idle")
        if db_run:
            status_val = "healthy" if db_run.status.value == "success" else db_run.status.value
        workers.append({
            "name": name,
            "status": "running" if status_val in ("healthy", "running", "idle") else "stopped",
            "last_run": last_run,
        })
    healthy = all(v.get("status") in ("healthy", "idle") for v in cache.values()) or all(
        w["status"] == "running" for w in workers
    )
    return {"healthy": healthy, "workers": workers}


@router.post("/paper/start")
def paper_start(store: StoreDep):
    store.update_settings("paper_trading_enabled", True)
    portfolio = store.resolve_paper_portfolio()
    portfolio.status = PortfolioStatus.ACTIVE
    store.update_portfolio(portfolio)
    return {"status": "active"}


@router.post("/paper/stop")
def paper_stop(store: StoreDep):
    store.update_settings("paper_trading_enabled", False)
    portfolio = store.resolve_paper_portfolio()
    portfolio.status = PortfolioStatus.HALTED
    store.update_portfolio(portfolio)
    return {"status": "halted"}


@router.get("/settings")
def get_settings(store: StoreDep):
    return store.settings_api_response()


@router.put("/settings")
def update_settings(store: StoreDep, payload: dict):
    store.update_settings_bulk(payload)
    return store.settings_api_response()
