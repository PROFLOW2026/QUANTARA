"""Post-activation verification for equal-asset multi-broker Live Sim."""

from __future__ import annotations

import json
import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "engine"))

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from quantara_engine.broker.execution_model import ExecutionModelVersion
from quantara_engine.broker.fees import load_fee_profile
from quantara_engine.broker.instrument_mapping import resolve_instrument_mapping_for_route
from quantara_engine.broker.live_execution_gate import REAL_BROKER_SUBMISSION_ENABLED
from quantara_engine.broker.margin_profiles import load_margin_profile
from quantara_engine.broker.reconciliation_orchestrator import is_broker_execution_allowed
from quantara_engine.broker.routing import route_to_broker
from quantara_engine.broker.vendor import BrokerVendor
from quantara_engine.competition.realistic_broker_rollout import active_research_portfolio_counts
from quantara_engine.domain.types import Direction
from quantara_engine.owner_portfolio.aggregation import aggregate_owner_portfolio
from quantara_engine.owner_portfolio.asset_allocation import list_asset_allocations
from quantara_engine.owner_portfolio.asset_risk import evaluate_asset_envelope_risk
from quantara_engine.owner_portfolio.global_risk import evaluate_owner_global_risk, symbol_risk_group
from quantara_engine.owner_portfolio.service import LIVE_SIM_OWNER_SLUG
from quantara_engine.persistence.store import TradingStore
from quantara_engine.risk.concentration import RISK_GROUPS


def _load_database_url() -> str:
    for line in ROOT.joinpath(".env").read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip() in ("DATABASE_URL", "DIRECT_URL"):
            return value.strip().strip('"')
    raise RuntimeError("DATABASE_URL not found")


def main() -> None:
    engine = create_engine(_load_database_url())
    session = sessionmaker(bind=engine)()
    store = TradingStore(session)

    flags = session.execute(
        text(
            """
            SELECT multi_broker_mode_enabled, equal_asset_allocation_enabled, target_capital
            FROM owner_trading_portfolios WHERE slug = :slug
            """
        ),
        {"slug": LIVE_SIM_OWNER_SLUG},
    ).mappings().first()

    brokers = session.execute(
        text(
            """
            SELECT ba.slug, ba.broker_vendor::text, ba.broker_environment::text,
                   pba.allocated_capital, pba.enabled, pba.is_legacy_primary,
                   ba.starting_cash, ba.cash, ba.balance, ba.equity,
                   ba.is_active, ba.account_state::text, ba.connection_state::text
            FROM portfolio_broker_accounts pba
            JOIN broker_accounts ba ON ba.id = pba.broker_account_id
            JOIN owner_trading_portfolios otp ON otp.id = pba.owner_portfolio_id
            WHERE otp.slug = :slug
            ORDER BY ba.slug
            """
        ),
        {"slug": LIVE_SIM_OWNER_SLUG},
    ).mappings().all()

    assets = list_asset_allocations(store, LIVE_SIM_OWNER_SLUG)
    snapshot = aggregate_owner_portfolio(store, slug=LIVE_SIM_OWNER_SLUG)

    routing_checks = []
    for sym, direction in (
        ("BTCUSD", "long"),
        ("BTCUSD", "short"),
        ("ETHUSD", "long"),
        ("ETHUSD", "short"),
        ("NVDA", "long"),
        ("NVDA", "short"),
        ("TSLA", "long"),
        ("AMD", "long"),
        ("COIN", "long"),
        ("GBPJPY", "long"),
        ("GBPJPY", "short"),
        ("XAUUSD", "long"),
        ("XAUUSD", "short"),
    ):
        route = route_to_broker(
            store,
            symbol=sym,
            direction=direction,
            execution_model=ExecutionModelVersion.REALISTIC_BROKER_V1,
            owner_portfolio_slug=LIVE_SIM_OWNER_SLUG,
        )
        expected_vendor = BrokerVendor.KRAKEN if sym in ("BTCUSD", "ETHUSD") else BrokerVendor.IBKR
        routing_checks.append(
            {
                "symbol": sym,
                "direction": direction,
                "vendor": route.broker_vendor.value,
                "account_slug": route.broker_account_slug,
                "pass": route.broker_vendor == expected_vendor
                and route.broker_account_slug
                in ("live-sim-ibkr-like", "live-sim-kraken-like"),
            }
        )

    margin_fee = {}
    for sym, vendor in (("NVDA", BrokerVendor.IBKR), ("BTCUSD", BrokerVendor.KRAKEN)):
        route = route_to_broker(
            store,
            symbol=sym,
            direction="long",
            execution_model=ExecutionModelVersion.REALISTIC_BROKER_V1,
            owner_portfolio_slug=LIVE_SIM_OWNER_SLUG,
        )
        margin = load_margin_profile(
            store,
            broker_vendor=vendor,
            execution_product=route.execution_product,
            instrument_symbol=sym,
        )
        fee = load_fee_profile(
            store,
            broker_vendor=vendor,
            execution_product=route.execution_product,
        )
        mapping = resolve_instrument_mapping_for_route(
            store,
            symbol=sym,
            broker_vendor=vendor.value,
            execution_product=route.execution_product,
        )
        margin_fee[sym] = {
            "margin": str(margin),
            "fee": str(fee),
            "mapping_symbol": mapping.canonical_symbol,
        }

    asset_risk = evaluate_asset_envelope_risk(
        store,
        owner_slug=LIVE_SIM_OWNER_SLUG,
        canonical_symbol="NVDA",
        incremental_sl_risk_usd=Decimal("100"),
    )
    owner_risk = evaluate_owner_global_risk(
        store,
        owner_slug=LIVE_SIM_OWNER_SLUG,
        symbol="NVDA",
        incremental_sl_risk_usd=Decimal("100"),
        broker_local_allowed=True,
    )
    crypto_group = RISK_GROUPS.get("CRYPTO_RISK", ())
    exec_allowed = {
        slug: is_broker_execution_allowed(store, slug)
        for slug in ("live-sim-ibkr-like", "live-sim-kraken-like", "live-sim-10k")
    }

    research = active_research_portfolio_counts(store)
    run = session.execute(
        text("SELECT id::text, status FROM paper_runs WHERE id = '231d0c57-e991-4008-914c-aac92ab2bf2c'")
    ).mappings().first()

    report = {
        "flags": dict(flags),
        "brokers": [dict(b) for b in brokers],
        "assets": [
            {
                "symbol": a.canonical_symbol,
                "starting": float(a.starting_allocated_capital),
                "current_cash": float(a.current_cash),
                "enabled": a.enabled,
                "vendor": a.broker_vendor,
            }
            for a in assets
        ],
        "owner_snapshot": {
            "total_equity": float(snapshot.total_equity) if snapshot else None,
            "allocated_capital_sum": float(snapshot.allocated_capital_sum) if snapshot else None,
            "enabled_broker_slugs": [s.slug for s in snapshot.broker_slices] if snapshot else [],
        },
        "routing_checks": routing_checks,
        "routing_all_pass": all(r["pass"] for r in routing_checks),
        "margin_fee": margin_fee,
        "asset_risk_allowed": asset_risk.allowed,
        "owner_risk_allowed": owner_risk.allowed,
        "crypto_risk_group": list(crypto_group),
        "crypto_cross_broker": {
            "BTCUSD": symbol_risk_group("BTCUSD"),
            "ETHUSD": symbol_risk_group("ETHUSD"),
            "COIN": symbol_risk_group("COIN"),
        },
        "execution_allowed": exec_allowed,
        "real_submission": REAL_BROKER_SUBMISSION_ENABLED,
        "research": {
            "run": dict(run) if run else None,
            "counts": research.__dict__,
        },
    }
    print(json.dumps(report, indent=2, default=str))


if __name__ == "__main__":
    main()
