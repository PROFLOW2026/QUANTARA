"""Live Sim V2 sizing context — owner risk budget, broker buying power, global SL gates."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from quantara_engine.live_sim.execution_routing import broker_account_row_by_id
from quantara_engine.live_sim.risk_policy import OpenRiskSnapshot, compute_open_sl_risk
from quantara_engine.owner_portfolio.aggregation import aggregate_owner_portfolio
from quantara_engine.owner_portfolio.asset_allocation import (
    asset_equity,
    get_asset_allocation_row,
    is_equal_asset_mode_active,
)
from quantara_engine.owner_portfolio.global_risk import compute_global_open_sl_risk
from quantara_engine.owner_portfolio.service import LIVE_SIM_OWNER_SLUG
from sqlalchemy import text

from quantara_engine.persistence.store import TradingStore


@dataclass(frozen=True)
class LiveSimV2SizingContext:
    """Inputs for risk-based Live Sim entry sizing (V2)."""

    risk_equity: Decimal
    gate_equity: Decimal
    leverage_equity: Decimal
    buying_power_cash: Decimal
    open_risk: OpenRiskSnapshot
    asset_row: dict | None
    broker_account_id: str
    owner_equity: Decimal
    soft_allocation: bool


def _global_open_risk_snapshot(store: TradingStore) -> OpenRiskSnapshot:
    snap = compute_global_open_sl_risk(store, LIVE_SIM_OWNER_SLUG)
    return OpenRiskSnapshot(
        total_sl_risk_usd=snap.total_sl_risk_usd,
        by_symbol=dict(snap.by_symbol),
        by_group=dict(snap.by_group),
    )


def resolve_live_sim_v2_sizing_context(
    store: TradingStore,
    *,
    symbol: str,
    account: dict,
    account_id: str,
) -> LiveSimV2SizingContext:
    """
    Owner-level risk budget with broker-level margin/leverage economics.

    Per-asset $1,250 envelopes remain for attribution; they must not cap target risk %.
    """
    owner = aggregate_owner_portfolio(store, slug=LIVE_SIM_OWNER_SLUG)
    owner_equity = Decimal(str(owner.total_equity if owner else account.get("equity") or 10000))

    broker_equity = Decimal(str(account.get("equity") or account.get("starting_cash") or 0))
    broker_cash = Decimal(str(account.get("cash") or broker_equity))

    asset_row = None
    routed_account_id = account_id
    if is_equal_asset_mode_active(store, LIVE_SIM_OWNER_SLUG):
        asset_row = get_asset_allocation_row(
            store, owner_slug=LIVE_SIM_OWNER_SLUG, canonical_symbol=symbol
        )
        if asset_row and asset_row.get("broker_account_id"):
            routed_account_id = str(asset_row["broker_account_id"])
            routed = broker_account_row_by_id(store, routed_account_id)
            if routed:
                broker_equity = Decimal(str(routed.get("equity") or routed.get("starting_cash") or 0))
                broker_cash = Decimal(str(routed.get("cash") or broker_equity))

    open_risk = _global_open_risk_snapshot(store)

    return LiveSimV2SizingContext(
        risk_equity=owner_equity,
        gate_equity=owner_equity,
        leverage_equity=broker_equity,
        buying_power_cash=broker_cash,
        open_risk=open_risk,
        asset_row=asset_row,
        broker_account_id=routed_account_id,
        owner_equity=owner_equity,
        soft_allocation=is_equal_asset_mode_active(store, LIVE_SIM_OWNER_SLUG),
    )


def idle_allocation_usd_on_broker(
    store: TradingStore,
    *,
    broker_account_id: str,
    trading_symbol: str,
) -> Decimal:
    """Sum cash in enabled asset slices on this broker excluding the active symbol."""
    if not is_equal_asset_mode_active(store, LIVE_SIM_OWNER_SLUG):
        return Decimal("0")
    sym = trading_symbol.upper().replace("/", "")
    rows = store.session.execute(
        text(
            """
            SELECT a.canonical_symbol, a.current_cash, a.unrealized_pnl, a.gross_exposure
            FROM owner_portfolio_asset_allocations a
            JOIN owner_trading_portfolios otp ON otp.id = a.owner_portfolio_id
            WHERE otp.slug = :slug
              AND a.enabled = TRUE
              AND a.broker_account_id = CAST(:aid AS uuid)
            """
        ),
        {"slug": LIVE_SIM_OWNER_SLUG, "aid": broker_account_id},
    ).mappings().all()
    idle = Decimal("0")
    for row in rows:
        if str(row["canonical_symbol"]).upper() == sym:
            continue
        exposure = Decimal(str(row["gross_exposure"] or 0))
        if exposure > 0:
            continue
        idle += asset_equity(dict(row))
    return idle
