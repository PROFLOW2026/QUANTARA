"""Owner portfolio service — allocations, validation, multi-broker activation."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy import text

from quantara_engine.broker.accounts import LIVE_SIM_10K_ACCOUNT_SLUG
from quantara_engine.broker.vendor import BrokerVendor
from quantara_engine.persistence.store import TradingStore

LIVE_SIM_OWNER_SLUG = "live-sim-owner"

IBKR_LIKE_SLUG = "live-sim-ibkr-like"
KRAKEN_LIKE_SLUG = "live-sim-kraken-like"


@dataclass(frozen=True)
class AllocationValidation:
    valid: bool
    target_capital: Decimal
    allocated_sum: Decimal
    remaining: Decimal
    message: str | None = None


def validate_allocation_sum(
    target_capital: Decimal,
    allocations: dict[str, Decimal],
) -> AllocationValidation:
    total = sum(allocations.values(), Decimal("0"))
    remaining = target_capital - total
    if total > target_capital:
        return AllocationValidation(
            valid=False,
            target_capital=target_capital,
            allocated_sum=total,
            remaining=remaining,
            message="allocated_capital_exceeds_target",
        )
    return AllocationValidation(
        valid=True,
        target_capital=target_capital,
        allocated_sum=total,
        remaining=remaining,
    )


class OwnerPortfolioService:
    def __init__(self, store: TradingStore) -> None:
        self.store = store

    def get_portfolio_row(self, slug: str) -> dict | None:
        try:
            return self.store.session.execute(
                text(
                    """
                    SELECT id::text, slug, name, base_currency, target_capital,
                           multi_broker_mode_enabled, global_execution_halted,
                           risk_settings, metadata, status::text
                    FROM owner_trading_portfolios WHERE slug = :slug
                    """
                ),
                {"slug": slug},
            ).mappings().first()
        except Exception:
            self.store.session.rollback()
            return None

    def list_allocations(self, slug: str) -> list[dict[str, Any]]:
        portfolio = self.get_portfolio_row(slug)
        if not portfolio:
            return []
        rows = self.store.session.execute(
            text(
                """
                SELECT pba.id::text, ba.id::text AS broker_account_id, ba.slug,
                       ba.broker_vendor::text, pba.allocated_capital, pba.allocation_pct,
                       pba.enabled, pba.is_legacy_primary, pba.label_he,
                       ba.cash, ba.equity
                FROM portfolio_broker_accounts pba
                JOIN broker_accounts ba ON ba.id = pba.broker_account_id
                WHERE pba.owner_portfolio_id = CAST(:pid AS uuid)
                ORDER BY pba.is_legacy_primary DESC, ba.slug
                """
            ),
            {"pid": portfolio["id"]},
        ).mappings().all()
        return [dict(r) for r in rows]

    def configure_multi_broker_allocations(
        self,
        slug: str,
        *,
        ibkr_allocation: Decimal,
        kraken_allocation: Decimal,
        activate: bool = False,
    ) -> dict[str, Any]:
        portfolio = self.get_portfolio_row(slug)
        if not portfolio:
            return {"ok": False, "error": "portfolio_not_found"}

        target = Decimal(str(portfolio["target_capital"]))
        validation = validate_allocation_sum(
            target,
            {"ibkr": ibkr_allocation, "kraken": kraken_allocation},
        )
        if activate and not validation.valid:
            return {
                "ok": False,
                "error": validation.message,
                "validation": validation.__dict__,
            }
        if activate and validation.allocated_sum != target:
            return {
                "ok": False,
                "error": "allocation_must_equal_target",
                "validation": validation.__dict__,
            }

        self._ensure_vendor_accounts(portfolio["id"], slug)
        self._update_vendor_allocation(portfolio["id"], IBKR_LIKE_SLUG, ibkr_allocation, activate)
        self._update_vendor_allocation(portfolio["id"], KRAKEN_LIKE_SLUG, kraken_allocation, activate)

        if activate:
            self.store.session.execute(
                text(
                    """
                    UPDATE owner_trading_portfolios
                    SET multi_broker_mode_enabled = TRUE, updated_at = NOW()
                    WHERE id = CAST(:pid AS uuid)
                    """
                ),
                {"pid": portfolio["id"]},
            )
            self.store.session.execute(
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

        self.store.session.commit()
        return {
            "ok": True,
            "activated": activate,
            "validation": {
                "valid": validation.valid,
                "target_capital": float(validation.target_capital),
                "allocated_sum": float(validation.allocated_sum),
                "remaining": float(validation.remaining),
            },
        }

    def _ensure_vendor_accounts(self, portfolio_id: str, owner_slug: str) -> None:
        for vendor_slug, vendor, label in (
            (IBKR_LIKE_SLUG, BrokerVendor.IBKR, "IBKR (סימולציה)"),
            (KRAKEN_LIKE_SLUG, BrokerVendor.KRAKEN, "Kraken (סימולציה)"),
        ):
            existing = self.store.session.execute(
                text("SELECT id::text FROM broker_accounts WHERE slug = :slug"),
                {"slug": vendor_slug},
            ).scalar()
            if not existing:
                self.store.session.execute(
                    text(
                        """
                        INSERT INTO broker_accounts (
                          slug, profile_slug, position_mode, starting_cash, cash, balance, equity,
                          spot_crypto_cash, is_active, pending_owner_reset, account_state,
                          broker_vendor, broker_environment, connection_state,
                          activated_at, risk_settings, account_metadata
                        ) VALUES (
                          :slug, 'quantara_live_sim_10k', 'netting',
                          0, 0, 0, 0, 0,
                          FALSE, FALSE, 'paused',
                          CAST(:vendor AS broker_vendor), 'SIMULATION', 'DISCONNECTED',
                          NULL, '{}'::jsonb,
                          jsonb_build_object('label_he', :label, 'owner_portfolio', :owner_slug)
                        )
                        """
                    ),
                    {"slug": vendor_slug, "vendor": vendor.value, "label": label, "owner_slug": owner_slug},
                )
                existing = self.store.session.execute(
                    text("SELECT id::text FROM broker_accounts WHERE slug = :slug"),
                    {"slug": vendor_slug},
                ).scalar()

            link = self.store.session.execute(
                text(
                    """
                    SELECT 1 FROM portfolio_broker_accounts
                    WHERE owner_portfolio_id = CAST(:pid AS uuid)
                      AND broker_account_id = CAST(:aid AS uuid)
                    """
                ),
                {"pid": portfolio_id, "aid": existing},
            ).scalar()
            if not link:
                self.store.session.execute(
                    text(
                        """
                        INSERT INTO portfolio_broker_accounts (
                          owner_portfolio_id, broker_account_id, allocated_capital,
                          enabled, is_legacy_primary, label_he
                        ) VALUES (
                          CAST(:pid AS uuid), CAST(:aid AS uuid), 0, FALSE, FALSE, :label
                        )
                        """
                    ),
                    {"pid": portfolio_id, "aid": existing, "label": label},
                )

    def _update_vendor_allocation(
        self,
        portfolio_id: str,
        account_slug: str,
        allocated: Decimal,
        activate: bool,
    ) -> None:
        self.store.session.execute(
            text(
                """
                UPDATE portfolio_broker_accounts pba
                SET allocated_capital = :alloc,
                    enabled = :enabled,
                    updated_at = NOW()
                FROM broker_accounts ba
                WHERE pba.broker_account_id = ba.id
                  AND pba.owner_portfolio_id = CAST(:pid AS uuid)
                  AND ba.slug = :slug
                """
            ),
            {
                "pid": portfolio_id,
                "slug": account_slug,
                "alloc": allocated,
                "enabled": activate and allocated > 0,
            },
        )

    def resolve_live_sim_broker_account_slug(self, owner_slug: str = LIVE_SIM_OWNER_SLUG) -> str:
        """Return active broker account slug for Live Sim execution."""
        portfolio = self.get_portfolio_row(owner_slug)
        if not portfolio:
            return LIVE_SIM_10K_ACCOUNT_SLUG
        if not portfolio.get("multi_broker_mode_enabled"):
            row = self.store.session.execute(
                text(
                    """
                    SELECT ba.slug
                    FROM portfolio_broker_accounts pba
                    JOIN broker_accounts ba ON ba.id = pba.broker_account_id
                    WHERE pba.owner_portfolio_id = CAST(:pid AS uuid)
                      AND pba.is_legacy_primary = TRUE
                      AND pba.enabled = TRUE
                    LIMIT 1
                    """
                ),
                {"pid": portfolio["id"]},
            ).scalar()
            return str(row) if row else LIVE_SIM_10K_ACCOUNT_SLUG
        return LIVE_SIM_10K_ACCOUNT_SLUG
