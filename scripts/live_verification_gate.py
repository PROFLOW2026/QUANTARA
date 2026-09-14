"""Final live verification gate — read-only except optional ETH precision repair."""

from __future__ import annotations

import importlib
import inspect
import json
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "engine"))

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from quantara_engine.live_sim.integrity_containment import is_live_sim_entries_blocked
from quantara_engine.persistence.store import TradingStore


def _load_database_url() -> str:
    env_path = ROOT / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            if key.strip() in ("DATABASE_URL", "DIRECT_URL"):
                return value.strip().strip('"').strip("'")
    url = __import__("os").environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL not configured")
    return url


def _code_loaded_verification() -> dict:
    checks = {}
    # entry authority
    from quantara_engine.live_sim import entry_authority as ea

    checks["entry_authority"] = hasattr(ea, "live_sim_physical_entry_succeeded")
    src = inspect.getsourcefile(ea)
    checks["entry_authority_path"] = src

    from quantara_engine.live_sim import close_authority as ca

    checks["close_authority"] = hasattr(ca, "finalize_live_sim_position_close")

    from quantara_engine.live_sim import integrity_containment as ic

    checks["containment_module"] = hasattr(ic, "is_live_sim_entries_blocked")

    from quantara_engine.broker import attribution as att

    checks["attribution_idempotency"] = "IntegrityError" in inspect.getsource(
        att.allocate_fill_to_strategy_legs
    )

    from quantara_engine.broker import execution_service as es

    checks["hwm_on_refresh"] = "update_high_water_mark" in inspect.getsource(
        es.BrokerExecutionService._refresh_account_from_db
    )

    from quantara_engine.market_data import registry as reg

    gbp = next(a for a in reg.list_target_assets() if a.db_symbol == "GBPJPY")
    checks["gbpjpy_tiingo_primary"] = gbp.primary_provider.value == "tiingo"

    from quantara_engine.learning import planned_vs_actual as pva

    checks["jpy_risk_conversion"] = hasattr(pva, "_sl_risk_usd")

    from quantara_engine.live_sim import allocator as alloc

    checks["allocator_containment_gate"] = "live_sim_integrity_containment" in inspect.getsource(
        alloc.maybe_allocate_live_sim
    )
    checks["allocator_preassign_spid"] = "strategy_position_id=pos_id" in inspect.getsource(
        alloc._execute_accepted_allocation
    )

    return checks


def _gbpjpy_candles(session) -> dict:
    rows = session.execute(
        text(
            """
            SELECT c.timeframe, MAX(c.timestamp) AS ts, c.source
            FROM candles c
            JOIN instruments i ON i.id = c.instrument_id
            WHERE i.symbol = 'GBPJPY'
            GROUP BY c.timeframe, c.source
            ORDER BY c.timeframe, c.source
            """
        )
    ).mappings().all()
    latest_1m = session.execute(
        text(
            """
            SELECT c.timestamp, c.source
            FROM candles c
            JOIN instruments i ON i.id = c.instrument_id
            WHERE i.symbol = 'GBPJPY' AND c.timeframe = '1m'
            ORDER BY c.timestamp DESC LIMIT 1
            """
        )
    ).mappings().first()
    by_tf: dict = {}
    for r in rows:
        by_tf.setdefault(r["timeframe"], []).append(
            {"ts": r["ts"].isoformat() if r["ts"] else None, "source": r["source"]}
        )
    return {
        "latest_1m": latest_1m["timestamp"].isoformat() if latest_1m else None,
        "latest_1m_source": latest_1m["source"] if latest_1m else None,
        "by_timeframe": by_tf,
    }


def _td_protection_count(session, since_iso: str) -> int:
    return int(
        session.execute(
            text(
                """
                SELECT COUNT(*) FROM settings_audit
                WHERE key LIKE '%twelvedata%' OR description ILIKE '%twelve%'
                """
            )
        ).scalar()
        or 0
    )


def _worker_status(session) -> dict:
    row = session.execute(
        text("SELECT value FROM settings WHERE key = 'worker_status:data_fetcher'")
    ).scalar()
    ncf = session.execute(
        text("SELECT value FROM settings WHERE key = 'worker_status:non_crypto_fast_protection'")
    ).scalar()
    fx_prot = session.execute(
        text("SELECT value FROM settings WHERE key = 'fx_protection:last_source'")
    ).scalar()
    return {
        "data_fetcher": row,
        "ncf": ncf,
        "fx_protection": fx_prot,
    }


def _eth_reconciliation(session) -> dict:
    pos = session.execute(
        text(
            """
            SELECT p.id::text, p.direction::text, p.quantity, p.entry_price,
                   p.stop_loss, p.take_profit, p.current_price, p.planned_sl_risk_usd,
                   ba.slug, ba.id::text AS account_id
            FROM live_sim_positions p
            JOIN instruments i ON i.id = p.instrument_id
            JOIN broker_accounts ba ON ba.id = p.broker_account_id
            WHERE p.status = 'open' AND i.symbol = 'ETHUSD'
            """
        )
    ).mappings().first()
    if not pos:
        return {"open": False}

    lots = session.execute(
        text(
            """
            SELECT id::text, direction::text, remaining_qty, entry_price, broker_fill_id::text
            FROM broker_attribution_lots
            WHERE strategy_position_id = CAST(:pid AS uuid) AND remaining_qty > 0
            """
        ),
        {"pid": pos["id"]},
    ).mappings().all()

    phys = session.execute(
        text(
            """
            SELECT net_quantity, average_price, mark_price, unrealized_pnl
            FROM broker_positions bp
            JOIN instruments i ON i.id = bp.instrument_id
            WHERE bp.broker_account_id = CAST(:aid AS uuid) AND i.symbol = 'ETHUSD'
            """
        ),
        {"aid": pos["account_id"]},
    ).mappings().first()

    def signed(d: str, q) -> Decimal:
        v = Decimal(str(q))
        return v if d == "long" else -v

    strat_signed = signed(pos["direction"], pos["quantity"])
    attr_signed = sum(signed(l["direction"], l["remaining_qty"]) for l in lots)
    phys_signed = Decimal(str(phys["net_quantity"])) if phys else Decimal("0")

    fills = session.execute(
        text(
            """
            SELECT f.id::text, f.fill_quantity, f.fill_price, o.order_purpose, o.direction::text
            FROM broker_fills f
            JOIN broker_orders o ON o.id = f.broker_order_id
            JOIN live_sim_allocation_log l ON l.broker_order_id = o.id
            WHERE l.live_sim_position_id = CAST(:pid AS uuid)
            ORDER BY f.filled_at
            """
        ),
        {"pid": pos["id"]},
    ).mappings().all()

    return {
        "position_id": pos["id"],
        "broker": pos["slug"],
        "direction": pos["direction"],
        "strategy_qty": str(pos["quantity"]),
        "strategy_signed": float(strat_signed),
        "attribution_signed": float(attr_signed),
        "physical_signed": float(phys_signed),
        "strategy_vs_attribution_diff": float(strat_signed - attr_signed),
        "attribution_vs_physical_diff": float(attr_signed - phys_signed),
        "entry": str(pos["entry_price"]),
        "mark": str(phys["mark_price"]) if phys else str(pos["current_price"]),
        "sl": str(pos["stop_loss"]),
        "tp": str(pos["take_profit"]) if pos["take_profit"] else None,
        "lots": [dict(l) for l in lots],
        "fills": [dict(f) for f in fills],
        "physical": dict(phys) if phys else None,
    }


def _financial_reconciliation(session) -> dict:
    from quantara_engine.owner_portfolio.aggregation import aggregate_owner_portfolio
    from quantara_engine.owner_portfolio.service import LIVE_SIM_OWNER_SLUG

    store = TradingStore(session)
    owner = aggregate_owner_portfolio(store, slug=LIVE_SIM_OWNER_SLUG)
    brokers = session.execute(
        text(
            """
            SELECT slug, equity, cash, balance, realized_pnl, unrealized_pnl, fees_paid
            FROM broker_accounts
            WHERE slug IN ('live-sim-ibkr-like', 'live-sim-kraken-like')
            """
        )
    ).mappings().all()
    from quantara_engine.owner_portfolio.asset_allocation import list_asset_allocations

    assets = list_asset_allocations(store, owner_slug=LIVE_SIM_OWNER_SLUG)

    ibkr = next((b for b in brokers if b["slug"] == "live-sim-ibkr-like"), None)
    kraken = next((b for b in brokers if b["slug"] == "live-sim-kraken-like"), None)
    broker_eq = Decimal(str(ibkr["equity"] or 0)) + Decimal(str(kraken["equity"] or 0))
    broker_real = Decimal(str(ibkr["realized_pnl"] or 0)) + Decimal(str(kraken["realized_pnl"] or 0))
    broker_unreal = Decimal(str(ibkr["unrealized_pnl"] or 0)) + Decimal(
        str(kraken["unrealized_pnl"] or 0)
    )
    asset_eq = sum(a.current_equity for a in assets if a.enabled)
    asset_real = sum(a.realized_pnl for a in assets if a.enabled)
    asset_unreal = sum(a.unrealized_pnl for a in assets if a.enabled)

    return {
        "owner": {
            "equity": float(owner.total_equity),
            "cash": float(owner.total_cash),
            "realized": float(owner.total_realized_pnl),
            "unrealized": float(owner.total_unrealized_pnl),
        },
        "ibkr": dict(ibkr) if ibkr else None,
        "kraken": dict(kraken) if kraken else None,
        "asset_sum_equity": float(asset_eq),
        "asset_sum_realized": float(asset_real),
        "asset_sum_unrealized": float(asset_unreal),
        "broker_vs_owner_equity_diff": float(broker_eq - owner.total_equity),
        "broker_vs_owner_realized_diff": float(broker_real - owner.total_realized_pnl),
        "broker_vs_owner_unrealized_diff": float(broker_unreal - owner.total_unrealized_pnl),
        "asset_vs_owner_equity_diff": float(asset_eq - owner.total_equity),
        "asset_vs_owner_realized_diff": float(asset_real - owner.total_realized_pnl),
        "asset_vs_owner_unrealized_diff": float(asset_unreal - owner.total_unrealized_pnl),
    }


def _hwm_report(session) -> dict:
    rows = session.execute(
        text(
            """
            SELECT slug, equity, risk_settings->>'high_water_mark' AS hwm
            FROM broker_accounts
            WHERE slug IN ('live-sim-ibkr-like', 'live-sim-kraken-like')
            """
        )
    ).mappings().all()
    out = {}
    for r in rows:
        eq = Decimal(str(r["equity"]))
        hwm = Decimal(str(r["hwm"] or eq))
        dd = max(Decimal("0"), (hwm - eq) / hwm * 100) if hwm > 0 else Decimal("0")
        out[r["slug"]] = {
            "equity": float(eq),
            "hwm": float(hwm),
            "drawdown_pct": float(dd),
        }
    owner_hwm = session.execute(
        text(
            """
            SELECT SUM(high_water_mark) FROM owner_portfolio_asset_allocations opa
            JOIN owner_trading_portfolios otp ON otp.id = opa.owner_portfolio_id
            WHERE otp.slug = 'live-sim-owner' AND opa.enabled = TRUE
            """
        )
    ).scalar()
    from quantara_engine.owner_portfolio.aggregation import aggregate_owner_portfolio
    from quantara_engine.owner_portfolio.service import LIVE_SIM_OWNER_SLUG

    owner_eq = aggregate_owner_portfolio(
        TradingStore(session), slug=LIVE_SIM_OWNER_SLUG
    ).total_equity
    oeq = Decimal(str(owner_eq or 0))
    ohwm = Decimal(str(owner_hwm or 0))
    out["owner"] = {
        "equity": float(oeq),
        "hwm": float(ohwm),
        "drawdown_pct": float(max(Decimal("0"), (ohwm - oeq) / ohwm * 100) if ohwm > 0 else 0),
    }
    return out


def _containment_proof(session) -> dict:
    store = TradingStore(session)
    blocked = is_live_sim_entries_blocked(store)
    containment = session.execute(
        text("SELECT value FROM settings WHERE key = 'live_sim_integrity_containment'")
    ).scalar()
    new_positions = session.execute(
        text(
            """
            SELECT COUNT(*) FROM live_sim_positions
            WHERE opened_at > (
              SELECT (value->>'activated_at')::timestamptz
              FROM settings WHERE key = 'live_sim_integrity_containment'
            )
            """
        )
    ).scalar()
    resumed = session.execute(
        text(
            """
            SELECT COUNT(*) FROM live_sim_allocation_log
            WHERE accepted = TRUE AND broker_order_id IS NOT NULL
              AND created_at > (
                SELECT (value->>'activated_at')::timestamptz
                FROM settings WHERE key = 'live_sim_integrity_containment'
              )
            """
        )
    ).scalar()
    return {
        "blocked": blocked,
        "containment": containment,
        "new_positions_since_containment": int(new_positions or 0),
        "new_filled_allocations_since_containment": int(resumed or 0),
    }


def _desync_counts(session) -> dict:
    shadow_open_broker_flat = session.execute(
        text(
            """
            SELECT COUNT(*) FROM live_sim_positions p
            JOIN broker_accounts ba ON ba.id = p.broker_account_id
            JOIN instruments i ON i.id = p.instrument_id
            LEFT JOIN broker_positions bp ON bp.broker_account_id = p.broker_account_id
              AND bp.instrument_id = p.instrument_id
            LEFT JOIN (
              SELECT strategy_position_id, SUM(remaining_qty) AS rem
              FROM broker_attribution_lots GROUP BY strategy_position_id
            ) l ON l.strategy_position_id = p.id
            WHERE p.status = 'open' AND ba.slug LIKE 'live-sim-%'
              AND COALESCE(l.rem, 0) = 0 AND COALESCE(ABS(bp.net_quantity), 0) = 0
            """
        )
    ).scalar()
    shadow_closed_broker_open = session.execute(
        text(
            """
            SELECT COUNT(*) FROM live_sim_positions p
            JOIN broker_accounts ba ON ba.id = p.broker_account_id
            JOIN instruments i ON i.id = p.instrument_id
            JOIN broker_positions bp ON bp.broker_account_id = p.broker_account_id
              AND bp.instrument_id = p.instrument_id
            WHERE p.status = 'closed' AND ba.slug LIKE 'live-sim-%'
              AND ABS(bp.net_quantity) > 0
            """
        )
    ).scalar()
    phys_zero_attr = session.execute(
        text(
            """
            SELECT COUNT(*) FROM broker_positions bp
            JOIN broker_accounts ba ON ba.id = bp.broker_account_id
            WHERE ba.slug LIKE 'live-sim-%' AND ABS(bp.net_quantity) > 0
              AND NOT EXISTS (
                SELECT 1 FROM broker_attribution_lots l
                WHERE l.broker_account_id = bp.broker_account_id
                  AND l.symbol = (
                    SELECT symbol FROM instruments WHERE id = bp.instrument_id
                  ) AND l.remaining_qty > 0
              )
            """
        )
    ).scalar()
    return {
        "shadow_open_broker_flat": int(shadow_open_broker_flat or 0),
        "shadow_closed_broker_open": int(shadow_closed_broker_open or 0),
        "physical_with_zero_attribution": int(phys_zero_attr or 0),
    }


def repair_eth_to_physical(*, dry_run: bool = True) -> dict:
    """Align strategy + attribution lot to broker physical qty from broker truth."""
    engine = create_engine(_load_database_url())
    Session = sessionmaker(bind=engine)
    session = Session()
    eth = _eth_reconciliation(session)
    if not eth.get("position_id"):
        session.close()
        return {"status": "no_open_eth"}

    phys = eth["physical"]
    if not phys:
        session.close()
        return {"status": "no_physical"}

    broker_qty = abs(Decimal(str(phys["net_quantity"])))
    broker_dir = "short" if Decimal(str(phys["net_quantity"])) < 0 else "long"
    pid = eth["position_id"]

    actions = []
    if Decimal(str(eth["strategy_qty"])) != broker_qty or eth["direction"] != broker_dir:
        actions.append(f"update_strategy:{pid}:qty={broker_qty}:dir={broker_dir}")

    lots = eth["lots"]
    active_lots = [l for l in lots if Decimal(str(l["remaining_qty"])) > 0]
    matching = [l for l in active_lots if l["direction"] == broker_dir]
    opposite = [l for l in active_lots if l["direction"] != broker_dir]

    for lot in opposite:
        actions.append(f"zero_opposite_lot:{lot['id']}")

    if len(matching) == 1:
        lot = matching[0]
        if Decimal(str(lot["remaining_qty"])) != broker_qty:
            actions.append(f"update_lot:{lot['id']}:qty={broker_qty}")
    elif len(matching) == 0 and broker_qty > 0:
        actions.append(f"create_lot_from_broker_truth:needs_primary_lot")
    elif len(matching) > 1:
        keep = max(matching, key=lambda l: Decimal(str(l["remaining_qty"])))
        actions.append(f"keep_lot:{keep['id']}:qty={broker_qty}")
        for lot in matching:
            if lot["id"] != keep["id"]:
                actions.append(f"zero_duplicate_lot:{lot['id']}")

    if not dry_run and actions:
        for lot in opposite + [l for l in matching if len(matching) > 1]:
            if any(a.startswith(f"keep_lot:{lot['id']}") for a in actions):
                continue
            if lot in opposite or (
                len(matching) > 1
                and lot["id"] != max(matching, key=lambda l: Decimal(str(l["remaining_qty"])))["id"]
            ):
                session.execute(
                    text(
                        "UPDATE broker_attribution_lots SET remaining_qty = 0 WHERE id = CAST(:id AS uuid)"
                    ),
                    {"id": lot["id"]},
                )
        if matching:
            keep = (
                max(matching, key=lambda l: Decimal(str(l["remaining_qty"])))
                if len(matching) > 1
                else matching[0]
            )
            session.execute(
                text(
                    """
                    UPDATE broker_attribution_lots
                    SET remaining_qty = :qty, direction = CAST(:dir AS direction),
                        strategy_position_id = CAST(:pid AS uuid)
                    WHERE id = CAST(:id AS uuid)
                    """
                ),
                {"qty": broker_qty, "dir": broker_dir, "id": keep["id"], "pid": pid},
            )
        session.execute(
            text(
                """
                UPDATE live_sim_positions
                SET quantity = :qty, direction = CAST(:dir AS direction), updated_at = NOW()
                WHERE id = CAST(:id AS uuid)
                """
            ),
            {"qty": broker_qty, "dir": broker_dir, "id": pid},
        )
        session.commit()

    session.close()
    return {"dry_run": dry_run, "broker_qty": str(broker_qty), "broker_dir": broker_dir, "actions": actions}


def run_full_report(*, repair_eth: bool = False) -> dict:
    engine = create_engine(_load_database_url())
    Session = sessionmaker(bind=engine)
    session = Session()

    report = {
        "verified_at_utc": datetime.now(timezone.utc).isoformat(),
        "code_loaded": _code_loaded_verification(),
        "containment": _containment_proof(session),
        "gbpjpy": _gbpjpy_candles(session),
        "worker": _worker_status(session),
        "eth_before": _eth_reconciliation(session),
        "desync": _desync_counts(session),
        "hwm": _hwm_report(session),
        "financial": _financial_reconciliation(session),
    }

    if repair_eth:
        report["eth_repair"] = repair_eth_to_physical(dry_run=False)
        report["eth_after"] = _eth_reconciliation(session)

    session.close()
    return report


if __name__ == "__main__":
    repair = "--repair-eth" in sys.argv
    print(json.dumps(run_full_report(repair_eth=repair), indent=2, default=str))
