"""Per-asset capital envelopes for equal-asset Live Sim mode."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy import text

from quantara_engine.broker.vendor import BrokerVendor
from quantara_engine.owner_portfolio.constants import (
    LIVE_SIM_EQUAL_ASSET_DEFINITIONS,
    LIVE_SIM_IBKR_TOTAL,
    LIVE_SIM_KRAKEN_TOTAL,
    LIVE_SIM_PER_ASSET_CAPITAL,
    LIVE_SIM_TARGET_CAPITAL,
)
from quantara_engine.broker.accounts import LIVE_SIM_10K_ACCOUNT_SLUG
from quantara_engine.owner_portfolio.service import IBKR_LIKE_SLUG, KRAKEN_LIKE_SLUG, OwnerPortfolioService
from quantara_engine.persistence.store import TradingStore


@dataclass(frozen=True)
class AssetAllocationRow:
    id: str
    canonical_symbol: str
    broker_account_id: str | None
    portfolio_broker_account_id: str | None
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
    enabled: bool
    label_he: str
    return_pct: Decimal


@dataclass(frozen=True)
class AssetAllocationValidation:
    valid: bool
    target_capital: Decimal
    asset_sum: Decimal
    ibkr_sum: Decimal
    kraken_sum: Decimal
    message: str | None = None


def asset_equity(row: dict[str, Any]) -> Decimal:
    cash = Decimal(str(row.get("current_cash") or 0))
    unrealized = Decimal(str(row.get("unrealized_pnl") or 0))
    return cash + unrealized


def validate_equal_asset_allocations(
    *,
    target_capital: Decimal = LIVE_SIM_TARGET_CAPITAL,
    per_asset: Decimal = LIVE_SIM_PER_ASSET_CAPITAL,
) -> AssetAllocationValidation:
    asset_sum = per_asset * len(LIVE_SIM_EQUAL_ASSET_DEFINITIONS)
    ibkr_sum = per_asset * sum(
        1 for vendor, _ in LIVE_SIM_EQUAL_ASSET_DEFINITIONS.values() if vendor == BrokerVendor.IBKR
    )
    kraken_sum = per_asset * sum(
        1 for vendor, _ in LIVE_SIM_EQUAL_ASSET_DEFINITIONS.values() if vendor == BrokerVendor.KRAKEN
    )
    if asset_sum != target_capital:
        return AssetAllocationValidation(
            valid=False,
            target_capital=target_capital,
            asset_sum=asset_sum,
            ibkr_sum=ibkr_sum,
            kraken_sum=kraken_sum,
            message="asset_sum_not_equal_target",
        )
    if ibkr_sum != LIVE_SIM_IBKR_TOTAL or kraken_sum != LIVE_SIM_KRAKEN_TOTAL:
        return AssetAllocationValidation(
            valid=False,
            target_capital=target_capital,
            asset_sum=asset_sum,
            ibkr_sum=ibkr_sum,
            kraken_sum=kraken_sum,
            message="broker_derived_totals_mismatch",
        )
    return AssetAllocationValidation(
        valid=True,
        target_capital=target_capital,
        asset_sum=asset_sum,
        ibkr_sum=ibkr_sum,
        kraken_sum=kraken_sum,
    )


def is_equal_asset_mode_active(store: TradingStore, owner_slug: str) -> bool:
    row = store.session.execute(
        text(
            """
            SELECT equal_asset_allocation_enabled, multi_broker_mode_enabled
            FROM owner_trading_portfolios WHERE slug = :slug
            """
        ),
        {"slug": owner_slug},
    ).mappings().first()
    if not row:
        return False
    return bool(row["equal_asset_allocation_enabled"] and row["multi_broker_mode_enabled"])


def is_equal_asset_configured(store: TradingStore, owner_slug: str) -> bool:
    portfolio = store.session.execute(
        text("SELECT id::text FROM owner_trading_portfolios WHERE slug = :slug"),
        {"slug": owner_slug},
    ).scalar()
    if not portfolio:
        return False
    count = store.session.execute(
        text(
            """
            SELECT COUNT(*) FROM owner_portfolio_asset_allocations
            WHERE owner_portfolio_id = CAST(:pid AS uuid)
            """
        ),
        {"pid": portfolio},
    ).scalar()
    return int(count or 0) >= len(LIVE_SIM_EQUAL_ASSET_DEFINITIONS)


def list_asset_allocations(store: TradingStore, owner_slug: str) -> list[AssetAllocationRow]:
    rows = store.session.execute(
        text(
            """
            SELECT a.id::text, a.canonical_symbol, a.broker_account_id::text,
                   a.portfolio_broker_account_id::text, ba.broker_vendor::text,
                   a.starting_allocated_capital, a.current_cash, a.realized_pnl,
                   a.unrealized_pnl, a.fees_paid, a.funding_paid, a.gross_exposure,
                   a.open_sl_risk_usd, a.trade_count, a.enabled, a.label_he
            FROM owner_portfolio_asset_allocations a
            LEFT JOIN broker_accounts ba ON ba.id = a.broker_account_id
            JOIN owner_trading_portfolios otp ON otp.id = a.owner_portfolio_id
            WHERE otp.slug = :slug
            ORDER BY a.canonical_symbol
            """
        ),
        {"slug": owner_slug},
    ).mappings().all()

    out: list[AssetAllocationRow] = []
    for row in rows:
        starting = Decimal(str(row["starting_allocated_capital"]))
        equity = asset_equity(dict(row))
        ret = (equity - starting) / starting * 100 if starting > 0 else Decimal("0")
        out.append(
            AssetAllocationRow(
                id=row["id"],
                canonical_symbol=row["canonical_symbol"],
                broker_account_id=row["broker_account_id"],
                portfolio_broker_account_id=row["portfolio_broker_account_id"],
                broker_vendor=row["broker_vendor"],
                starting_allocated_capital=starting,
                current_cash=Decimal(str(row["current_cash"] or 0)),
                current_equity=equity,
                realized_pnl=Decimal(str(row["realized_pnl"] or 0)),
                unrealized_pnl=Decimal(str(row["unrealized_pnl"] or 0)),
                fees_paid=Decimal(str(row["fees_paid"] or 0)),
                funding_paid=Decimal(str(row["funding_paid"] or 0)),
                gross_exposure=Decimal(str(row["gross_exposure"] or 0)),
                open_sl_risk_usd=Decimal(str(row["open_sl_risk_usd"] or 0)),
                trade_count=int(row["trade_count"] or 0),
                enabled=bool(row["enabled"]),
                label_he=str(row["label_he"] or row["canonical_symbol"]),
                return_pct=ret,
            )
        )
    return out


def get_asset_allocation_row(
    store: TradingStore,
    *,
    owner_slug: str,
    canonical_symbol: str,
) -> dict[str, Any] | None:
    sym = canonical_symbol.upper().replace("/", "")
    row = store.session.execute(
        text(
            """
            SELECT a.id::text, a.canonical_symbol, a.broker_account_id::text,
                   a.starting_allocated_capital, a.current_cash, a.realized_pnl,
                   a.unrealized_pnl, a.fees_paid, a.funding_paid, a.gross_exposure,
                   a.open_sl_risk_usd, a.trade_count, a.enabled, a.label_he
            FROM owner_portfolio_asset_allocations a
            JOIN owner_trading_portfolios otp ON otp.id = a.owner_portfolio_id
            WHERE otp.slug = :slug AND a.canonical_symbol = :sym
            """
        ),
        {"slug": owner_slug, "sym": sym},
    ).mappings().first()
    return dict(row) if row else None


def derive_broker_totals_from_assets(
    store: TradingStore,
    owner_slug: str,
) -> dict[str, Decimal]:
    rows = store.session.execute(
        text(
            """
            SELECT ba.broker_vendor::text AS vendor,
                   COALESCE(SUM(a.starting_allocated_capital), 0) AS total
            FROM owner_portfolio_asset_allocations a
            JOIN owner_trading_portfolios otp ON otp.id = a.owner_portfolio_id
            LEFT JOIN broker_accounts ba ON ba.id = a.broker_account_id
            WHERE otp.slug = :slug AND a.enabled = TRUE
            GROUP BY ba.broker_vendor
            """
        ),
        {"slug": owner_slug},
    ).mappings().all()
    return {str(r["vendor"]): Decimal(str(r["total"])) for r in rows if r["vendor"]}


def sync_broker_allocations_from_assets(store: TradingStore, owner_slug: str) -> None:
    """Derive broker allocated_capital from enabled asset rows (single financial truth)."""
    portfolio = store.session.execute(
        text("SELECT id::text FROM owner_trading_portfolios WHERE slug = :slug"),
        {"slug": owner_slug},
    ).scalar()
    if not portfolio:
        return

    totals = derive_broker_totals_from_assets(store, owner_slug)
    ibkr_total = totals.get("IBKR", Decimal("0"))
    kraken_total = totals.get("KRAKEN", Decimal("0"))

    for slug, total in ((IBKR_LIKE_SLUG, ibkr_total), (KRAKEN_LIKE_SLUG, kraken_total)):
        store.session.execute(
            text(
                """
                UPDATE portfolio_broker_accounts pba
                SET allocated_capital = :total, updated_at = NOW()
                FROM broker_accounts ba
                WHERE pba.broker_account_id = ba.id
                  AND pba.owner_portfolio_id = CAST(:pid AS uuid)
                  AND ba.slug = :slug
                """
            ),
            {"pid": portfolio, "slug": slug, "total": total},
        )


def _initialize_simulation_vendor_accounts_on_activation(store: TradingStore) -> None:
    """Seed SIMULATION vendor broker accounts with allocated capital and connector-ready state."""
    for slug, capital in (
        (IBKR_LIKE_SLUG, LIVE_SIM_IBKR_TOTAL),
        (KRAKEN_LIKE_SLUG, LIVE_SIM_KRAKEN_TOTAL),
    ):
        store.session.execute(
            text(
                """
                UPDATE broker_accounts
                SET starting_cash = :cap,
                    cash = :cap,
                    balance = :cap,
                    equity = :cap,
                    spot_crypto_cash = CASE
                      WHEN broker_vendor = 'KRAKEN' THEN :cap
                      ELSE spot_crypto_cash
                    END,
                    is_active = TRUE,
                    pending_owner_reset = FALSE,
                    account_state = 'active',
                    connection_state = 'CONNECTED',
                    activated_at = COALESCE(activated_at, NOW()),
                    updated_at = NOW()
                WHERE slug = :slug
                  AND broker_environment = 'SIMULATION'
                """
            ),
            {"slug": slug, "cap": capital},
        )

    store.session.execute(
        text(
            """
            UPDATE broker_accounts
            SET is_active = FALSE,
                account_state = 'paused',
                connection_state = 'DISCONNECTED',
                updated_at = NOW()
            WHERE slug = :slug
            """
        ),
        {"slug": LIVE_SIM_10K_ACCOUNT_SLUG},
    )


def configure_equal_asset_allocations(
    store: TradingStore,
    owner_slug: str,
    *,
    activate: bool = False,
) -> dict[str, Any]:
    """Apply owner-selected 8 × $1,250 allocation; keep multi-broker OFF unless activate."""
    svc = OwnerPortfolioService(store)
    portfolio = svc.get_portfolio_row(owner_slug)
    if not portfolio:
        return {"ok": False, "error": "portfolio_not_found"}

    validation = validate_equal_asset_allocations(
        target_capital=Decimal(str(portfolio["target_capital"]))
    )
    if not validation.valid:
        return {"ok": False, "error": validation.message, "validation": validation.__dict__}

    if activate and portfolio.get("multi_broker_mode_enabled") and portfolio.get(
        "equal_asset_allocation_enabled"
    ):
        return {
            "ok": True,
            "activated": True,
            "validation": validation.__dict__,
            "per_asset_capital": float(LIVE_SIM_PER_ASSET_CAPITAL),
            "ibkr_total": float(LIVE_SIM_IBKR_TOTAL),
            "kraken_total": float(LIVE_SIM_KRAKEN_TOTAL),
        }

    try:
        svc._ensure_vendor_accounts(portfolio["id"], owner_slug)
        store.session.flush()

        if activate:
            store.session.execute(
                text(
                    """
                    UPDATE owner_portfolio_asset_allocations
                    SET enabled = FALSE, updated_at = NOW()
                    WHERE owner_portfolio_id = CAST(:pid AS uuid)
                    """
                ),
                {"pid": portfolio["id"]},
            )

        link_by_vendor: dict[str, str] = {}
        ba_by_vendor: dict[str, str] = {}
        rows = store.session.execute(
            text(
                """
                SELECT pba.id::text, ba.id::text AS broker_account_id, ba.broker_vendor::text
                FROM portfolio_broker_accounts pba
                JOIN broker_accounts ba ON ba.id = pba.broker_account_id
                WHERE pba.owner_portfolio_id = CAST(:pid AS uuid)
                  AND NOT pba.is_legacy_primary
                """
            ),
            {"pid": portfolio["id"]},
        ).mappings().all()
        for row in rows:
            link_by_vendor[str(row["broker_vendor"])] = row["id"]
            ba_by_vendor[str(row["broker_vendor"])] = row["broker_account_id"]

        per_asset = LIVE_SIM_PER_ASSET_CAPITAL
        for symbol, (vendor, label) in LIVE_SIM_EQUAL_ASSET_DEFINITIONS.items():
            vendor_key = vendor.value
            store.session.execute(
                text(
                    """
                    INSERT INTO owner_portfolio_asset_allocations (
                      owner_portfolio_id, canonical_symbol, portfolio_broker_account_id,
                      broker_account_id, starting_allocated_capital, current_cash,
                      enabled, label_he
                    ) VALUES (
                      CAST(:pid AS uuid), :sym,
                      CAST(:link AS uuid), CAST(:ba AS uuid),
                      :cap, :cap, :enabled, :label
                    )
                    ON CONFLICT (owner_portfolio_id, canonical_symbol) DO UPDATE SET
                      portfolio_broker_account_id = EXCLUDED.portfolio_broker_account_id,
                      broker_account_id = EXCLUDED.broker_account_id,
                      starting_allocated_capital = EXCLUDED.starting_allocated_capital,
                      current_cash = CASE
                        WHEN owner_portfolio_asset_allocations.trade_count = 0
                        THEN EXCLUDED.current_cash
                        ELSE owner_portfolio_asset_allocations.current_cash
                      END,
                      enabled = EXCLUDED.enabled,
                      label_he = EXCLUDED.label_he,
                      updated_at = NOW()
                    """
                ),
                {
                    "pid": portfolio["id"],
                    "sym": symbol,
                    "link": link_by_vendor.get(vendor_key),
                    "ba": ba_by_vendor.get(vendor_key),
                    "cap": per_asset,
                    "enabled": activate,
                    "label": label,
                },
            )

        sync_broker_allocations_from_assets(store, owner_slug)

        if activate:
            store.session.execute(
                text(
                    """
                    UPDATE portfolio_broker_accounts
                    SET enabled = FALSE, updated_at = NOW()
                    WHERE owner_portfolio_id = CAST(:pid AS uuid)
                      AND is_legacy_primary = TRUE
                    """
                ),
                {"pid": portfolio["id"]},
            )

        store.session.execute(
            text(
                """
                UPDATE portfolio_broker_accounts pba
                SET allocated_capital = :ibkr, enabled = :enabled, updated_at = NOW()
                FROM broker_accounts ba
                WHERE pba.broker_account_id = ba.id
                  AND pba.owner_portfolio_id = CAST(:pid AS uuid)
                  AND ba.slug = :slug
                """
            ),
            {
                "pid": portfolio["id"],
                "slug": IBKR_LIKE_SLUG,
                "ibkr": LIVE_SIM_IBKR_TOTAL,
                "enabled": activate,
            },
        )
        store.session.execute(
            text(
                """
                UPDATE portfolio_broker_accounts pba
                SET allocated_capital = :kraken, enabled = :enabled, updated_at = NOW()
                FROM broker_accounts ba
                WHERE pba.broker_account_id = ba.id
                  AND pba.owner_portfolio_id = CAST(:pid AS uuid)
                  AND ba.slug = :slug
                """
            ),
            {
                "pid": portfolio["id"],
                "slug": KRAKEN_LIKE_SLUG,
                "kraken": LIVE_SIM_KRAKEN_TOTAL,
                "enabled": activate,
            },
        )

        if activate:
            _initialize_simulation_vendor_accounts_on_activation(store)
            store.session.execute(
                text(
                    """
                    UPDATE owner_trading_portfolios
                    SET multi_broker_mode_enabled = TRUE,
                        equal_asset_allocation_enabled = TRUE,
                        updated_at = NOW()
                    WHERE id = CAST(:pid AS uuid)
                    """
                ),
                {"pid": portfolio["id"]},
            )
        else:
            store.session.execute(
                text(
                    """
                    UPDATE owner_trading_portfolios
                    SET metadata = COALESCE(metadata, '{}'::jsonb) || jsonb_build_object(
                          'equal_asset_allocation', jsonb_build_object(
                            'configured', TRUE,
                            'per_asset_capital', 1250,
                            'ibkr_total', 7500,
                            'kraken_total', 2500
                          )
                        ),
                        updated_at = NOW()
                    WHERE id = CAST(:pid AS uuid)
                    """
                ),
                {"pid": portfolio["id"]},
            )

        store.session.commit()
    except Exception as exc:
        store.session.rollback()
        return {"ok": False, "error": "allocation_db_rejected", "detail": str(exc)}

    return {
        "ok": True,
        "activated": activate,
        "validation": validation.__dict__,
        "per_asset_capital": float(LIVE_SIM_PER_ASSET_CAPITAL),
        "ibkr_total": float(LIVE_SIM_IBKR_TOTAL),
        "kraken_total": float(LIVE_SIM_KRAKEN_TOTAL),
    }


def deactivate_equal_asset_allocations(store: TradingStore, owner_slug: str) -> dict[str, Any]:
    """Turn off equal-asset multi-broker mode and restore legacy single-account draft state."""
    portfolio = store.session.execute(
        text(
            """
            SELECT id::text FROM owner_trading_portfolios WHERE slug = :slug
            """
        ),
        {"slug": owner_slug},
    ).scalar()
    if not portfolio:
        return {"ok": False, "error": "portfolio_not_found"}

    try:
        store.session.execute(
            text(
                """
                UPDATE owner_trading_portfolios
                SET multi_broker_mode_enabled = FALSE,
                    equal_asset_allocation_enabled = FALSE,
                    updated_at = NOW()
                WHERE id = CAST(:pid AS uuid)
                """
            ),
            {"pid": portfolio},
        )
        store.session.execute(
            text(
                """
                UPDATE owner_portfolio_asset_allocations
                SET enabled = FALSE, updated_at = NOW()
                WHERE owner_portfolio_id = CAST(:pid AS uuid)
                """
            ),
            {"pid": portfolio},
        )
        store.session.execute(
            text(
                """
                UPDATE portfolio_broker_accounts
                SET enabled = FALSE, updated_at = NOW()
                WHERE owner_portfolio_id = CAST(:pid AS uuid)
                  AND NOT is_legacy_primary
                """
            ),
            {"pid": portfolio},
        )
        store.session.execute(
            text(
                """
                UPDATE portfolio_broker_accounts
                SET enabled = TRUE, updated_at = NOW()
                WHERE owner_portfolio_id = CAST(:pid AS uuid)
                  AND is_legacy_primary = TRUE
                """
            ),
            {"pid": portfolio},
        )
        store.session.execute(
            text(
                """
                UPDATE broker_accounts
                SET is_active = TRUE,
                    account_state = 'active',
                    connection_state = 'CONNECTED',
                    updated_at = NOW()
                WHERE slug = :slug
                """
            ),
            {"slug": LIVE_SIM_10K_ACCOUNT_SLUG},
        )
        store.session.execute(
            text(
                """
                UPDATE broker_accounts
                SET is_active = FALSE,
                    account_state = 'paused',
                    connection_state = 'DISCONNECTED',
                    starting_cash = 0,
                    cash = 0,
                    balance = 0,
                    equity = 0,
                    spot_crypto_cash = 0,
                    updated_at = NOW()
                WHERE slug IN (:ibkr, :kraken)
                  AND broker_environment = 'SIMULATION'
                """
            ),
            {"ibkr": IBKR_LIKE_SLUG, "kraken": KRAKEN_LIKE_SLUG},
        )
        store.session.commit()
    except Exception as exc:
        store.session.rollback()
        return {"ok": False, "error": "deactivation_db_rejected", "detail": str(exc)}

    return {"ok": True, "deactivated": True}


def audit_legacy_live_sim_before_equal_asset_activation(store: TradingStore) -> dict[str, Any]:
    """Report legacy Live Sim state — do not invent attribution for existing activity."""
    from quantara_engine.broker.accounts import LIVE_SIM_10K_ACCOUNT_SLUG

    row = store.session.execute(
        text(
            """
            SELECT id::text, equity, realized_pnl
            FROM broker_accounts WHERE slug = :slug
            """
        ),
        {"slug": LIVE_SIM_10K_ACCOUNT_SLUG},
    ).mappings().first()
    if not row:
        return {"available": False}

    aid = row["id"]
    open_positions = int(
        store.session.execute(
            text(
                """
                SELECT COUNT(*) FROM live_sim_positions
                WHERE broker_account_id = CAST(:aid AS uuid) AND status = 'open'
                """
            ),
            {"aid": aid},
        ).scalar()
        or 0
    )
    closed_positions = int(
        store.session.execute(
            text(
                """
                SELECT COUNT(*) FROM live_sim_positions
                WHERE broker_account_id = CAST(:aid AS uuid) AND status = 'closed'
                """
            ),
            {"aid": aid},
        ).scalar()
        or 0
    )
    fills = int(
        store.session.execute(
            text(
                """
                SELECT COUNT(*) FROM broker_fills f
                JOIN broker_orders o ON o.id = f.broker_order_id
                WHERE o.broker_account_id = CAST(:aid AS uuid)
                """
            ),
            {"aid": aid},
        ).scalar()
        or 0
    )
    return {
        "available": True,
        "legacy_equity": float(row["equity"] or 0),
        "legacy_realized_pnl": float(row["realized_pnl"] or 0),
        "open_positions": open_positions,
        "closed_positions": closed_positions,
        "broker_fills": fills,
        "requires_manual_attribution_review": open_positions > 0 or fills > 0,
    }
