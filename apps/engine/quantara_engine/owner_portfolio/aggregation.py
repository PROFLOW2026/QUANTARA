"""Derive owner-level aggregates from broker account truth — no double counting."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import text

from quantara_engine.broker.vendor import BrokerVendor, vendor_label_he
from quantara_engine.persistence.store import TradingStore


@dataclass
class BrokerAccountSlice:
    broker_account_id: str
    slug: str
    broker_vendor: str
    label_he: str
    allocated_capital: Decimal | None
    enabled: bool
    is_legacy_primary: bool
    cash: Decimal
    equity: Decimal
    balance: Decimal
    realized_pnl: Decimal
    unrealized_pnl: Decimal
    initial_margin_used: Decimal
    available_margin: Decimal
    gross_exposure: Decimal
    net_exposure: Decimal
    connection_state: str
    reconciliation_halted: bool


@dataclass
class AssetAllocationSlice:
    canonical_symbol: str
    label_he: str
    broker_vendor: str | None
    starting_allocated_capital: Decimal
    current_cash: Decimal
    current_equity: Decimal
    realized_pnl: Decimal
    unrealized_pnl: Decimal
    fees_paid: Decimal
    funding_paid: Decimal
    gross_exposure: Decimal
    open_sl_risk_usd: Decimal
    trade_count: int
    return_pct: Decimal
    enabled: bool


@dataclass
class OwnerPortfolioSnapshot:
    portfolio_id: str
    slug: str
    name: str
    target_capital: Decimal
    multi_broker_mode_enabled: bool
    equal_asset_allocation_enabled: bool
    global_execution_halted: bool
    base_currency: str
    total_cash: Decimal
    total_equity: Decimal
    total_balance: Decimal
    total_realized_pnl: Decimal
    total_unrealized_pnl: Decimal
    total_initial_margin_used: Decimal
    total_available_capital: Decimal
    total_gross_exposure: Decimal
    total_net_exposure: Decimal
    allocated_capital_sum: Decimal
    allocation_remaining: Decimal
    broker_slices: list[BrokerAccountSlice] = field(default_factory=list)
    asset_slices: list[AssetAllocationSlice] = field(default_factory=list)


def aggregate_owner_portfolio(store: TradingStore, *, slug: str) -> OwnerPortfolioSnapshot | None:
    portfolio = store.session.execute(
        text(
            """
            SELECT id::text, slug, name, base_currency, target_capital,
                   multi_broker_mode_enabled, equal_asset_allocation_enabled,
                   global_execution_halted
            FROM owner_trading_portfolios
            WHERE slug = :slug
            """
        ),
        {"slug": slug},
    ).mappings().first()
    if not portfolio:
        return None

    rows = store.session.execute(
        text(
            """
            SELECT ba.id::text, ba.slug, ba.broker_vendor::text, ba.cash, ba.equity, ba.balance,
                   ba.realized_pnl, ba.unrealized_pnl, ba.initial_margin_used, ba.available_margin,
                   ba.gross_exposure, ba.net_exposure, ba.connection_state::text,
                   ba.reconciliation_halted,
                   pba.allocated_capital, pba.enabled, pba.is_legacy_primary, pba.label_he
            FROM portfolio_broker_accounts pba
            JOIN broker_accounts ba ON ba.id = pba.broker_account_id
            WHERE pba.owner_portfolio_id = CAST(:pid AS uuid)
            ORDER BY pba.is_legacy_primary DESC, ba.slug
            """
        ),
        {"pid": portfolio["id"]},
    ).mappings().all()

    slices: list[BrokerAccountSlice] = []
    totals = {
        "cash": Decimal("0"),
        "equity": Decimal("0"),
        "balance": Decimal("0"),
        "realized": Decimal("0"),
        "unrealized": Decimal("0"),
        "margin": Decimal("0"),
        "available": Decimal("0"),
        "gross": Decimal("0"),
        "net": Decimal("0"),
        "allocated": Decimal("0"),
    }

    for row in rows:
        if not row["enabled"]:
            continue
        allocated = (
            Decimal(str(row["allocated_capital"]))
            if row["allocated_capital"] is not None
            else None
        )
        if allocated is not None:
            totals["allocated"] += allocated
        vendor = str(row["broker_vendor"])
        label = row.get("label_he") or vendor_label_he(vendor)
        slice_row = BrokerAccountSlice(
            broker_account_id=row["id"],
            slug=row["slug"],
            broker_vendor=vendor,
            label_he=str(label),
            allocated_capital=allocated,
            enabled=bool(row["enabled"]),
            is_legacy_primary=bool(row["is_legacy_primary"]),
            cash=Decimal(str(row["cash"] or 0)),
            equity=Decimal(str(row["equity"] or 0)),
            balance=Decimal(str(row["balance"] or 0)),
            realized_pnl=Decimal(str(row["realized_pnl"] or 0)),
            unrealized_pnl=Decimal(str(row["unrealized_pnl"] or 0)),
            initial_margin_used=Decimal(str(row["initial_margin_used"] or 0)),
            available_margin=Decimal(str(row["available_margin"] or 0)),
            gross_exposure=Decimal(str(row["gross_exposure"] or 0)),
            net_exposure=Decimal(str(row["net_exposure"] or 0)),
            connection_state=str(row["connection_state"]),
            reconciliation_halted=bool(row["reconciliation_halted"]),
        )
        slices.append(slice_row)
        totals["cash"] += slice_row.cash
        totals["equity"] += slice_row.equity
        totals["balance"] += slice_row.balance
        totals["realized"] += slice_row.realized_pnl
        totals["unrealized"] += slice_row.unrealized_pnl
        totals["margin"] += slice_row.initial_margin_used
        totals["available"] += slice_row.available_margin
        totals["gross"] += slice_row.gross_exposure
        totals["net"] += slice_row.net_exposure

    from quantara_engine.owner_portfolio.asset_allocation import list_asset_allocations

    asset_rows = list_asset_allocations(store, owner_slug=slug)
    asset_slices = [
        AssetAllocationSlice(
            canonical_symbol=a.canonical_symbol,
            label_he=a.label_he,
            broker_vendor=a.broker_vendor,
            starting_allocated_capital=a.starting_allocated_capital,
            current_cash=a.current_cash,
            current_equity=a.current_equity,
            realized_pnl=a.realized_pnl,
            unrealized_pnl=a.unrealized_pnl,
            fees_paid=a.fees_paid,
            funding_paid=a.funding_paid,
            gross_exposure=a.gross_exposure,
            open_sl_risk_usd=a.open_sl_risk_usd,
            trade_count=a.trade_count,
            return_pct=a.return_pct,
            enabled=a.enabled,
        )
        for a in asset_rows
    ]

    equal_asset = bool(portfolio.get("equal_asset_allocation_enabled"))
    configured_assets = len(asset_rows) > 0
    if equal_asset and configured_assets:
        from quantara_engine.owner_portfolio.asset_ledger import aggregate_owner_from_assets

        asset_totals = aggregate_owner_from_assets(store, owner_slug=slug)
        totals["equity"] = asset_totals["equity"]
        totals["realized"] = asset_totals["realized_pnl"]
        totals["unrealized"] = asset_totals["unrealized_pnl"]
        totals["gross"] = asset_totals["gross_exposure"]
        totals["allocated"] = asset_totals["allocated"]

    target = Decimal(str(portfolio["target_capital"]))
    return OwnerPortfolioSnapshot(
        portfolio_id=portfolio["id"],
        slug=portfolio["slug"],
        name=portfolio["name"],
        target_capital=target,
        multi_broker_mode_enabled=bool(portfolio["multi_broker_mode_enabled"]),
        equal_asset_allocation_enabled=equal_asset,
        global_execution_halted=bool(portfolio["global_execution_halted"]),
        base_currency=str(portfolio["base_currency"]),
        total_cash=totals["cash"],
        total_equity=totals["equity"],
        total_balance=totals["balance"],
        total_realized_pnl=totals["realized"],
        total_unrealized_pnl=totals["unrealized"],
        total_initial_margin_used=totals["margin"],
        total_available_capital=totals["available"],
        total_gross_exposure=totals["gross"],
        total_net_exposure=totals["net"],
        allocated_capital_sum=totals["allocated"],
        allocation_remaining=target - totals["allocated"],
        broker_slices=slices,
        asset_slices=asset_slices,
    )
