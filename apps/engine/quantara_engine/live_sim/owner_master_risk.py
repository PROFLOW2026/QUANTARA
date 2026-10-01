"""Owner Master portfolio drawdown — canonical gate for multi-broker Live Sim."""

from __future__ import annotations

import json
from decimal import Decimal

from sqlalchemy import text

from quantara_engine.broker.accounts import LIVE_SIM_10K_ACCOUNT_SLUG
from quantara_engine.live_sim.risk_policy import (
    GateResult,
    LiveSimRiskSettings,
    evaluate_drawdown_gate,
    load_risk_settings,
)
from quantara_engine.owner_portfolio.aggregation import aggregate_owner_portfolio
from quantara_engine.owner_portfolio.service import LIVE_SIM_OWNER_SLUG
from quantara_engine.persistence.store import TradingStore


def _owner_portfolio_row(store: TradingStore) -> dict | None:
    row = store.session.execute(
        text(
            """
            SELECT id::text, target_capital, risk_settings, multi_broker_mode_enabled
            FROM owner_trading_portfolios WHERE slug = :slug
            """
        ),
        {"slug": LIVE_SIM_OWNER_SLUG},
    ).mappings().first()
    return dict(row) if row else None


def owner_master_equity(store: TradingStore) -> Decimal:
    snap = aggregate_owner_portfolio(store, slug=LIVE_SIM_OWNER_SLUG)
    if snap:
        return Decimal(str(snap.total_equity))
    legacy = store.session.execute(
        text(
            """
            SELECT equity FROM broker_accounts WHERE slug = :slug
            """
        ),
        {"slug": LIVE_SIM_10K_ACCOUNT_SLUG},
    ).scalar()
    return Decimal(str(legacy or 10_000))


def load_owner_master_risk_settings(store: TradingStore) -> LiveSimRiskSettings:
    """Risk settings + HWM for the single $10K owner portfolio."""
    equity = owner_master_equity(store)
    row = _owner_portfolio_row(store)
    raw: dict = {}
    starting = Decimal("10000")
    if row:
        starting = Decimal(str(row.get("target_capital") or 10_000))
        raw = dict(row.get("risk_settings") or {})
        if isinstance(raw, str):
            raw = json.loads(raw) if raw else {}
    if not raw:
        legacy = store.session.execute(
            text(
                """
                SELECT starting_cash, equity, risk_settings
                FROM broker_accounts WHERE slug = :slug
                """
            ),
            {"slug": LIVE_SIM_10K_ACCOUNT_SLUG},
        ).mappings().first()
        if legacy:
            starting = Decimal(str(legacy.get("starting_cash") or 10_000))
            equity = Decimal(str(legacy.get("equity") or equity))
            raw = dict(legacy.get("risk_settings") or {})
            if isinstance(raw, str):
                raw = json.loads(raw) if raw else {}
    account_row = {
        "equity": equity,
        "starting_cash": starting,
        "risk_settings": raw,
    }
    return load_risk_settings(account_row)


def update_owner_high_water_mark(store: TradingStore, equity: Decimal | None = None) -> Decimal:
    """Persist owner Master HWM on owner_trading_portfolios (multi-broker canonical)."""
    eq = equity if equity is not None else owner_master_equity(store)
    row = _owner_portfolio_row(store)
    if not row:
        return eq
    raw = dict(row.get("risk_settings") or {})
    if isinstance(raw, str):
        raw = json.loads(raw) if raw else {}
    hwm = Decimal(str(raw.get("high_water_mark") or eq))
    if eq > hwm:
        hwm = eq
        raw = {**raw, "high_water_mark": float(hwm)}
        store.session.execute(
            text(
                """
                UPDATE owner_trading_portfolios
                SET risk_settings = CAST(:settings AS jsonb), updated_at = NOW()
                WHERE id = CAST(:id AS uuid)
                """
            ),
            {"id": row["id"], "settings": json.dumps(raw)},
        )
    return hwm


def evaluate_owner_master_drawdown_gate(store: TradingStore) -> GateResult:
    """Global DRAWDOWN_GATE using owner aggregate equity vs owner Master HWM."""
    equity = owner_master_equity(store)
    settings = load_owner_master_risk_settings(store)
    hwm = update_owner_high_water_mark(store, equity)
    settings = LiveSimRiskSettings(
        risk_per_trade_pct=settings.risk_per_trade_pct,
        max_total_open_sl_risk_pct=settings.max_total_open_sl_risk_pct,
        max_symbol_sl_risk_pct=settings.max_symbol_sl_risk_pct,
        max_group_sl_risk_pct=settings.max_group_sl_risk_pct,
        daily_loss_gate_pct=settings.daily_loss_gate_pct,
        max_drawdown_gate_pct=settings.max_drawdown_gate_pct,
        concentration_mode=settings.concentration_mode,
        high_water_mark=hwm,
        daily_start_equity=settings.daily_start_equity,
        daily_start_date=settings.daily_start_date,
    )
    return evaluate_drawdown_gate(
        equity=equity,
        high_water_mark=settings.high_water_mark,
        max_drawdown_gate_pct=settings.max_drawdown_gate_pct,
        scope_label="owner master drawdown",
    )
