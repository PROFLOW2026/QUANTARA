"""Owner-global risk enforcement across linked broker accounts."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import text

from quantara_engine.owner_portfolio.aggregation import OwnerPortfolioSnapshot, aggregate_owner_portfolio
from quantara_engine.persistence.store import TradingStore
from quantara_engine.risk.concentration import RISK_GROUPS


@dataclass(frozen=True)
class GlobalRiskSnapshot:
    total_sl_risk_usd: Decimal
    by_symbol: dict[str, Decimal]
    by_group: dict[str, Decimal]
    gross_exposure_usd: Decimal
    net_exposure_usd: Decimal


@dataclass(frozen=True)
class GlobalRiskVerdict:
    allowed: bool
    reason: str | None = None
    layer: str = "owner_global"


def symbol_risk_group(symbol: str) -> str | None:
    sym = symbol.upper().replace("/", "")
    for group, assets in RISK_GROUPS.items():
        if sym in assets:
            return group
    return None


def compute_global_open_sl_risk(store: TradingStore, owner_slug: str) -> GlobalRiskSnapshot:
    """Aggregate open SL risk across all enabled broker accounts in owner portfolio."""
    portfolio = aggregate_owner_portfolio(store, slug=owner_slug)
    if not portfolio:
        return GlobalRiskSnapshot(Decimal("0"), {}, {}, Decimal("0"), Decimal("0"))

    account_ids = [s.broker_account_id for s in portfolio.broker_slices if s.enabled]
    if not account_ids:
        return GlobalRiskSnapshot(Decimal("0"), {}, {}, Decimal("0"), Decimal("0"))

    rows = store.session.execute(
        text(
            """
            SELECT i.symbol, p.planned_sl_risk_usd, p.broker_account_id::text
            FROM live_sim_positions p
            JOIN instruments i ON i.id = p.instrument_id
            WHERE p.broker_account_id = ANY(CAST(:aids AS uuid[]))
              AND p.status = 'open'
            """
        ),
        {"aids": account_ids},
    ).mappings().all()

    by_symbol: dict[str, Decimal] = {}
    by_group: dict[str, Decimal] = {}
    total = Decimal("0")
    for row in rows:
        sym = str(row["symbol"]).upper()
        risk = Decimal(str(row["planned_sl_risk_usd"] or 0))
        total += risk
        by_symbol[sym] = by_symbol.get(sym, Decimal("0")) + risk
        grp = symbol_risk_group(sym)
        if grp:
            by_group[grp] = by_group.get(grp, Decimal("0")) + risk

    return GlobalRiskSnapshot(
        total_sl_risk_usd=total,
        by_symbol=by_symbol,
        by_group=by_group,
        gross_exposure_usd=portfolio.total_gross_exposure,
        net_exposure_usd=portfolio.total_net_exposure,
    )


def _load_owner_risk_settings(portfolio_row: dict) -> dict:
    raw = portfolio_row.get("risk_settings") or {}
    if isinstance(raw, str):
        import json

        raw = json.loads(raw) if raw else {}
    return raw


def evaluate_owner_global_risk(
    store: TradingStore,
    *,
    owner_slug: str,
    symbol: str,
    incremental_sl_risk_usd: Decimal,
    broker_local_allowed: bool = True,
) -> GlobalRiskVerdict:
    """Evaluate owner-global gates. Trade must pass broker-local AND global."""
    portfolio_row = store.session.execute(
        text(
            """
            SELECT id::text, target_capital, multi_broker_mode_enabled,
                   equal_asset_allocation_enabled, global_execution_halted, risk_settings
            FROM owner_trading_portfolios WHERE slug = :slug
            """
        ),
        {"slug": owner_slug},
    ).mappings().first()

    if not portfolio_row:
        return GlobalRiskVerdict(allowed=True, reason=None, layer="owner_global_skipped")

    if portfolio_row.get("global_execution_halted"):
        return GlobalRiskVerdict(allowed=False, reason="global_execution_halted", layer="owner_global")

    if not portfolio_row.get("multi_broker_mode_enabled"):
        return GlobalRiskVerdict(allowed=True, reason="legacy_single_account", layer="owner_global")

    snapshot = aggregate_owner_portfolio(store, slug=owner_slug)
    if not snapshot:
        return GlobalRiskVerdict(allowed=True)

    settings = _load_owner_risk_settings(dict(portfolio_row))
    if portfolio_row.get("equal_asset_allocation_enabled"):
        from quantara_engine.owner_portfolio.asset_ledger import aggregate_owner_from_assets

        asset_totals = aggregate_owner_from_assets(store, owner_slug)
        equity = asset_totals["equity"]
    else:
        equity = snapshot.total_equity
    if equity <= 0:
        return GlobalRiskVerdict(allowed=False, reason="owner_equity_non_positive")

    risk = compute_global_open_sl_risk(store, owner_slug)
    sym = symbol.upper().replace("/", "")

    max_total_pct = Decimal(str(settings.get("max_total_open_sl_risk_pct", 3)))
    max_symbol_pct = Decimal(str(settings.get("max_symbol_sl_risk_pct", 1)))
    max_group_pct = Decimal(str(settings.get("max_group_sl_risk_pct", 2)))
    daily_loss_pct = Decimal(str(settings.get("daily_loss_gate_pct", 2)))
    max_dd_pct = Decimal(str(settings.get("max_drawdown_gate_pct", 10)))

    projected_total = risk.total_sl_risk_usd + incremental_sl_risk_usd
    if projected_total / equity * 100 > max_total_pct:
        return GlobalRiskVerdict(allowed=False, reason="owner_max_total_sl_risk")

    projected_symbol = risk.by_symbol.get(sym, Decimal("0")) + incremental_sl_risk_usd
    if projected_symbol / equity * 100 > max_symbol_pct:
        return GlobalRiskVerdict(allowed=False, reason="owner_max_symbol_sl_risk")

    grp = symbol_risk_group(sym)
    if grp:
        projected_group = risk.by_group.get(grp, Decimal("0")) + incremental_sl_risk_usd
        if projected_group / equity * 100 > max_group_pct:
            return GlobalRiskVerdict(allowed=False, reason="owner_max_group_sl_risk")

    hwm = Decimal(str(settings.get("high_water_mark", snapshot.target_capital)))
    if hwm > 0 and equity < hwm:
        dd_pct = (hwm - equity) / hwm * 100
        if dd_pct > max_dd_pct:
            return GlobalRiskVerdict(allowed=False, reason="owner_max_drawdown")

    daily_start = Decimal(str(settings.get("daily_start_equity", equity)))
    if daily_start > 0 and equity < daily_start:
        loss_pct = (daily_start - equity) / daily_start * 100
        if loss_pct > daily_loss_pct:
            return GlobalRiskVerdict(allowed=False, reason="owner_daily_loss_gate")

    if not broker_local_allowed:
        return GlobalRiskVerdict(allowed=False, reason="broker_local_rejected", layer="broker_local")

    untrusted = [
        s
        for s in snapshot.broker_slices
        if s.enabled
        and (
            s.reconciliation_halted
            or s.connection_state in ("HALTED", "RECONCILIATION_REQUIRED", "DISCONNECTED")
        )
    ]
    if untrusted and len(untrusted) == len([s for s in snapshot.broker_slices if s.enabled]):
        return GlobalRiskVerdict(allowed=False, reason="all_brokers_untrusted")

    return GlobalRiskVerdict(allowed=True)
