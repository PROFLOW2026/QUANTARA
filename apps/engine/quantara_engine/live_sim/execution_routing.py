"""Live Sim multi-broker execution routing — resolve target account per symbol/direction."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import text

from quantara_engine.broker.accounts import (
    LIVE_SIM_10K_ACCOUNT_SLUG,
    LIVE_SIM_IBKR_LIKE_SLUG,
    LIVE_SIM_KRAKEN_LIKE_SLUG,
)
from quantara_engine.broker.execution_model import ExecutionModelVersion, parse_execution_model
from quantara_engine.broker.routing import BrokerRouteDecision, route_to_broker
from quantara_engine.owner_portfolio.asset_allocation import is_equal_asset_mode_active
from quantara_engine.owner_portfolio.service import LIVE_SIM_OWNER_SLUG
from quantara_engine.persistence.store import TradingStore

_LEGACY_EXECUTION_BLOCK_REASON = "legacy_live_sim_execution_disabled"


@dataclass(frozen=True)
class LiveSimRuntimeContext:
    account: dict | None
    account_id: str | None
    account_slug: str
    route: BrokerRouteDecision | None
    legacy_blocked: bool = False


def is_multi_broker_live_sim_active(store: TradingStore, owner_slug: str = LIVE_SIM_OWNER_SLUG) -> bool:
    return is_equal_asset_mode_active(store, owner_slug)


def legacy_execution_blocked(store: TradingStore, account_slug: str) -> bool:
    """Fail-closed: legacy live-sim-10k must not accept new execution while multi-broker is on."""
    if account_slug != LIVE_SIM_10K_ACCOUNT_SLUG:
        return False
    return is_multi_broker_live_sim_active(store)


def assert_live_sim_execution_target_allowed(
    store: TradingStore, account_slug: str
) -> tuple[bool, str]:
    if legacy_execution_blocked(store, account_slug):
        return False, _LEGACY_EXECUTION_BLOCK_REASON
    return True, ""


def list_active_live_sim_broker_account_slugs(
    store: TradingStore, owner_slug: str = LIVE_SIM_OWNER_SLUG
) -> list[str]:
    if is_multi_broker_live_sim_active(store, owner_slug):
        return [LIVE_SIM_IBKR_LIKE_SLUG, LIVE_SIM_KRAKEN_LIKE_SLUG]
    return [LIVE_SIM_10K_ACCOUNT_SLUG]


def list_active_live_sim_broker_account_ids(
    store: TradingStore, owner_slug: str = LIVE_SIM_OWNER_SLUG
) -> list[str]:
    ids: list[str] = []
    for slug in list_active_live_sim_broker_account_slugs(store, owner_slug):
        row = broker_account_row(store, slug)
        if row and row.get("id"):
            ids.append(str(row["id"]))
    return ids


def broker_account_row(store: TradingStore, slug: str) -> dict | None:
    return store.session.execute(
        text(
            """
            SELECT id::text, slug, equity, balance, cash, spot_crypto_cash, unrealized_pnl,
                   realized_pnl, starting_cash, is_active, pending_owner_reset,
                   account_state::text, activated_at, risk_settings,
                   execution_model::text AS execution_model
            FROM broker_accounts WHERE slug = :slug
            """
        ),
        {"slug": slug},
    ).mappings().first()


def broker_account_row_by_id(store: TradingStore, account_id: str) -> dict | None:
    return store.session.execute(
        text(
            """
            SELECT id::text, slug, equity, balance, cash, spot_crypto_cash, unrealized_pnl,
                   realized_pnl, starting_cash, is_active, pending_owner_reset,
                   account_state::text, activated_at, risk_settings,
                   execution_model::text AS execution_model
            FROM broker_accounts WHERE id = CAST(:aid AS uuid)
            """
        ),
        {"aid": account_id},
    ).mappings().first()


def resolve_execution_model_for_slug(store: TradingStore, account_slug: str) -> ExecutionModelVersion:
    row = broker_account_row(store, account_slug)
    if not row or not row.get("execution_model"):
        return ExecutionModelVersion.LEGACY_SPOT_LIMITED
    return parse_execution_model(str(row["execution_model"]))


def resolve_live_sim_execution_route(
    store: TradingStore,
    symbol: str,
    direction: str,
    *,
    is_close: bool = False,
    owner_slug: str = LIVE_SIM_OWNER_SLUG,
) -> BrokerRouteDecision:
    account_slug = LIVE_SIM_10K_ACCOUNT_SLUG
    if is_multi_broker_live_sim_active(store, owner_slug):
        # Vendor accounts use realistic broker execution under equal-asset mode.
        model = ExecutionModelVersion.REALISTIC_BROKER_V1
    else:
        model = resolve_execution_model_for_slug(store, account_slug)
    return route_to_broker(
        store,
        symbol=symbol,
        direction=direction,
        execution_model=model,
        owner_portfolio_slug=owner_slug if is_multi_broker_live_sim_active(store, owner_slug) else None,
        is_close=is_close,
    )


def resolve_live_sim_runtime_context(
    store: TradingStore,
    symbol: str,
    direction: str,
    *,
    is_close: bool = False,
    owner_slug: str = LIVE_SIM_OWNER_SLUG,
) -> LiveSimRuntimeContext:
    if is_multi_broker_live_sim_active(store, owner_slug):
        route = resolve_live_sim_execution_route(
            store, symbol, direction, is_close=is_close, owner_slug=owner_slug
        )
        slug = route.broker_account_slug or LIVE_SIM_10K_ACCOUNT_SLUG
        if legacy_execution_blocked(store, slug):
            return LiveSimRuntimeContext(
                account=None,
                account_id=None,
                account_slug=slug,
                route=route,
                legacy_blocked=True,
            )
        account = broker_account_row(store, slug) if slug else None
        return LiveSimRuntimeContext(
            account=dict(account) if account else None,
            account_id=route.broker_account_id or (account["id"] if account else None),
            account_slug=slug,
            route=route,
        )

    account = broker_account_row(store, LIVE_SIM_10K_ACCOUNT_SLUG)
    return LiveSimRuntimeContext(
        account=dict(account) if account else None,
        account_id=account["id"] if account else None,
        account_slug=LIVE_SIM_10K_ACCOUNT_SLUG,
        route=None,
    )


def query_open_live_sim_position_rows(
    store: TradingStore,
    *,
    account_ids: list[str] | None = None,
) -> list[dict]:
    ids = account_ids or list_active_live_sim_broker_account_ids(store)
    if not ids:
        return []
    rows = store.session.execute(
        text(
            """
            SELECT p.id::text, p.instrument_id::text, p.direction::text, p.quantity,
                   p.entry_price, p.stop_loss, p.take_profit, p.timeframe,
                   p.canonical_opportunity_key, p.broker_account_id::text,
                   i.symbol, ba.slug AS broker_account_slug
            FROM live_sim_positions p
            JOIN instruments i ON i.id = p.instrument_id
            JOIN broker_accounts ba ON ba.id = p.broker_account_id
            WHERE p.status = 'open'
              AND p.broker_account_id = ANY(CAST(:aids AS uuid[]))
            """
        ),
        {"aids": ids},
    ).mappings().all()
    return [dict(row) for row in rows]
