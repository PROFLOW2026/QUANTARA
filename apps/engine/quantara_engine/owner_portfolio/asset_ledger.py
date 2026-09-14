"""Asset envelope ledger — isolated PnL, fees, and funding attribution."""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import text

from quantara_engine.owner_portfolio.asset_allocation import asset_equity, get_asset_allocation_row
from quantara_engine.persistence.store import TradingStore


def apply_asset_fill_impact(
    store: TradingStore,
    *,
    owner_slug: str,
    canonical_symbol: str,
    realized_pnl_delta: Decimal = Decimal("0"),
    fee_delta: Decimal = Decimal("0"),
    funding_delta: Decimal = Decimal("0"),
    increment_trade_count: bool = False,
) -> None:
    """Apply realized PnL and costs to one asset envelope only."""
    sym = canonical_symbol.upper().replace("/", "")
    row = get_asset_allocation_row(store, owner_slug=owner_slug, canonical_symbol=sym)
    if not row:
        return

    store.session.execute(
        text(
            """
            UPDATE owner_portfolio_asset_allocations
            SET current_cash = current_cash + :realized - :fee - :funding,
                realized_pnl = realized_pnl + :realized,
                fees_paid = fees_paid + :fee,
                funding_paid = funding_paid + :funding,
                trade_count = trade_count + :trade_inc,
                updated_at = NOW()
            WHERE id = CAST(:id AS uuid)
            """
        ),
        {
            "id": row["id"],
            "realized": realized_pnl_delta,
            "fee": fee_delta,
            "funding": funding_delta,
            "trade_inc": 1 if increment_trade_count else 0,
        },
    )


def refresh_asset_market_state(
    store: TradingStore,
    *,
    owner_slug: str,
    canonical_symbol: str,
    unrealized_pnl: Decimal,
    gross_exposure: Decimal,
    open_sl_risk_usd: Decimal,
) -> None:
    sym = canonical_symbol.upper().replace("/", "")
    store.session.execute(
        text(
            """
            UPDATE owner_portfolio_asset_allocations a
            SET unrealized_pnl = :unrealized,
                gross_exposure = :exposure,
                open_sl_risk_usd = :sl_risk,
                updated_at = NOW()
            FROM owner_trading_portfolios otp
            WHERE a.owner_portfolio_id = otp.id
              AND otp.slug = :slug
              AND a.canonical_symbol = :sym
            """
        ),
        {
            "slug": owner_slug,
            "sym": sym,
            "unrealized": unrealized_pnl,
            "exposure": gross_exposure,
            "sl_risk": open_sl_risk_usd,
        },
    )


def refresh_all_asset_states_from_positions(
    store: TradingStore,
    owner_slug: str,
) -> None:
    """Recompute per-asset unrealized/exposure/SL from canonical broker_positions.

    live_sim_positions is strategy attribution only; broker_positions are financial truth.
    Also bumps asset high-water mark monotonically when equity rises.
    """
    from quantara_engine.live_sim.asset_gate_settings import update_asset_high_water_mark

    rows = store.session.execute(
        text(
            """
            SELECT a.id::text AS asset_id, a.canonical_symbol,
                   a.broker_account_id::text AS aid,
                   a.current_cash, a.high_water_mark
            FROM owner_portfolio_asset_allocations a
            JOIN owner_trading_portfolios otp ON otp.id = a.owner_portfolio_id
            WHERE otp.slug = :slug AND a.enabled = TRUE
            """
        ),
        {"slug": owner_slug},
    ).mappings().all()

    for row in rows:
        sym = row["canonical_symbol"]
        aid = row["aid"]
        if not aid:
            continue
        # Broker position MTM is financial authority for this asset's symbol.
        bp = store.session.execute(
            text(
                """
                SELECT
                  COALESCE(SUM(bp.unrealized_pnl), 0) AS unrealized,
                  COALESCE(SUM(ABS(bp.net_quantity) * COALESCE(bp.mark_price, bp.average_price)), 0)
                    AS exposure
                FROM broker_positions bp
                JOIN instruments i ON i.id = bp.instrument_id
                WHERE bp.broker_account_id = CAST(:aid AS uuid)
                  AND i.symbol = :sym
                  AND bp.net_quantity <> 0
                """
            ),
            {"aid": aid, "sym": sym},
        ).mappings().first()
        sl = store.session.execute(
            text(
                """
                SELECT COALESCE(SUM(p.planned_sl_risk_usd), 0) AS sl_risk
                FROM live_sim_positions p
                JOIN instruments i ON i.id = p.instrument_id
                WHERE p.broker_account_id = CAST(:aid AS uuid)
                  AND p.status = 'open'
                  AND i.symbol = :sym
                """
            ),
            {"aid": aid, "sym": sym},
        ).mappings().first()
        unrealized = Decimal(str(bp["unrealized"] or 0)) if bp else Decimal("0")
        exposure = Decimal(str(bp["exposure"] or 0)) if bp else Decimal("0")
        sl_risk = Decimal(str(sl["sl_risk"] or 0)) if sl else Decimal("0")
        refresh_asset_market_state(
            store,
            owner_slug=owner_slug,
            canonical_symbol=sym,
            unrealized_pnl=unrealized,
            gross_exposure=exposure,
            open_sl_risk_usd=sl_risk,
        )
        equity = Decimal(str(row["current_cash"] or 0)) + unrealized
        update_asset_high_water_mark(store, row["asset_id"], equity)


def aggregate_assets_by_broker(
    store: TradingStore,
    owner_slug: str,
) -> dict[str, dict[str, Decimal]]:
    rows = store.session.execute(
        text(
            """
            SELECT ba.broker_vendor::text AS vendor,
                   COALESCE(SUM(a.current_cash + a.unrealized_pnl), 0) AS equity,
                   COALESCE(SUM(a.realized_pnl), 0) AS realized,
                   COALESCE(SUM(a.unrealized_pnl), 0) AS unrealized,
                   COALESCE(SUM(a.fees_paid), 0) AS fees,
                   COALESCE(SUM(a.funding_paid), 0) AS funding,
                   COALESCE(SUM(a.starting_allocated_capital), 0) AS allocated
            FROM owner_portfolio_asset_allocations a
            JOIN owner_trading_portfolios otp ON otp.id = a.owner_portfolio_id
            LEFT JOIN broker_accounts ba ON ba.id = a.broker_account_id
            WHERE otp.slug = :slug AND a.enabled = TRUE
            GROUP BY ba.broker_vendor
            """
        ),
        {"slug": owner_slug},
    ).mappings().all()
    return {
        str(r["vendor"]): {
            "equity": Decimal(str(r["equity"])),
            "realized_pnl": Decimal(str(r["realized"])),
            "unrealized_pnl": Decimal(str(r["unrealized"])),
            "fees_paid": Decimal(str(r["fees"])),
            "funding_paid": Decimal(str(r["funding"])),
            "allocated": Decimal(str(r["allocated"])),
        }
        for r in rows
        if r["vendor"]
    }


def aggregate_owner_from_assets(
    store: TradingStore,
    owner_slug: str,
) -> dict[str, Decimal]:
    rows = list_asset_rows_raw(store, owner_slug)
    totals = {
        "equity": Decimal("0"),
        "realized_pnl": Decimal("0"),
        "unrealized_pnl": Decimal("0"),
        "fees_paid": Decimal("0"),
        "funding_paid": Decimal("0"),
        "allocated": Decimal("0"),
        "gross_exposure": Decimal("0"),
        "open_sl_risk_usd": Decimal("0"),
    }
    for row in rows:
        if not row.get("enabled"):
            continue
        totals["equity"] += asset_equity(row)
        totals["realized_pnl"] += Decimal(str(row.get("realized_pnl") or 0))
        totals["unrealized_pnl"] += Decimal(str(row.get("unrealized_pnl") or 0))
        totals["fees_paid"] += Decimal(str(row.get("fees_paid") or 0))
        totals["funding_paid"] += Decimal(str(row.get("funding_paid") or 0))
        totals["allocated"] += Decimal(str(row.get("starting_allocated_capital") or 0))
        totals["gross_exposure"] += Decimal(str(row.get("gross_exposure") or 0))
        totals["open_sl_risk_usd"] += Decimal(str(row.get("open_sl_risk_usd") or 0))
    return totals


def list_asset_rows_raw(store: TradingStore, owner_slug: str) -> list[dict]:
    rows = store.session.execute(
        text(
            """
            SELECT a.*, otp.slug AS owner_slug
            FROM owner_portfolio_asset_allocations a
            JOIN owner_trading_portfolios otp ON otp.id = a.owner_portfolio_id
            WHERE otp.slug = :slug
            ORDER BY a.canonical_symbol
            """
        ),
        {"slug": owner_slug},
    ).mappings().all()
    return [dict(r) for r in rows]


def asset_available_cash(store: TradingStore, *, owner_slug: str, canonical_symbol: str) -> Decimal:
    row = get_asset_allocation_row(store, owner_slug=owner_slug, canonical_symbol=canonical_symbol)
    if not row:
        return Decimal("0")
    return Decimal(str(row.get("current_cash") or 0))
