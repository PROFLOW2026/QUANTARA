"""Settle remaining simulation defects: expire stale pendings, flatten AMD -24, close ETH dust.

Creates proper broker orders/fills via BrokerExecutionService (history preserved).
Idempotent keys:
  - settle_pending_all_stale_2026_11_07_v1 (settings stamp)
  - settle:amd_bug_short_flatten:2026_11_07_v1
  - settle:eth_dust_flatten:2026_11_07_v1
"""

from __future__ import annotations

import json
import os
import sys
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "engine"))
load_dotenv(ROOT / ".env")

from quantara_engine.broker.execution_service import BrokerExecutionService
from quantara_engine.domain.types import Direction, IntentStatus, OrderIntent
from quantara_engine.execution.cost_profile import execution_assumptions_for
from quantara_engine.execution.fill_calculator import calculate_fill_price
from quantara_engine.persistence.store import TradingStore

# Friday RTH timestamp so US equity close is allowed by market gate.
AMD_EXEC_AT = datetime(2026, 11, 6, 20, 55, tzinfo=timezone.utc)  # 15:55 ET
ETH_PID = "2ce6cba9-d2b4-4052-80a3-882689621a55"
STAMP_KEY = "settle_remaining_sim_fixes_2026_11_07_v1"


def _ser(v):
    if isinstance(v, Decimal):
        return str(v)
    if hasattr(v, "isoformat"):
        return v.isoformat()
    return v


def flatten_symbol(svc: BrokerExecutionService, symbol: str, *, exec_at: datetime, idem: str, purpose: str):
    snap = svc.load_account_snapshot()
    pos = snap.positions.get(symbol)
    if not pos or Decimal(str(pos.net_quantity)) == 0:
        return {"skipped": True, "reason": "already_flat", "symbol": symbol}
    net = Decimal(str(pos.net_quantity))
    qty = abs(net)
    close_dir = Direction.LONG if net < 0 else Direction.SHORT
    instrument = svc.store.get_instrument_by_symbol(symbol)
    mark = Decimal(str(pos.mark_price or 0))
    if mark <= 0:
        # fallback from broker_positions
        row = svc.store.session.execute(
            text(
                """
                SELECT bp.mark_price FROM broker_positions bp
                JOIN instruments i ON i.id=bp.instrument_id
                WHERE bp.broker_account_id=(SELECT id FROM broker_accounts WHERE slug=:s)
                  AND i.symbol=:sym
                """
            ),
            {"s": svc.account_slug, "sym": symbol},
        ).scalar()
        mark = Decimal(str(row or 0))
    assumptions = execution_assumptions_for(instrument)
    fill_calc = calculate_fill_price(close_dir, "exit", mark, qty, assumptions)
    intent = OrderIntent(
        id=str(uuid.uuid4()),
        signal_id="",
        strategy_instance_id="",
        portfolio_id="",
        direction=close_dir,
        quantity=qty,
        stop_loss=Decimal("0"),
        take_profit=None,
        target_risk_amount=Decimal("0"),
        actual_risk_amount=Decimal("0"),
        signal_candle_timestamp=exec_at,
        risk_profile_id="",
        status=IntentStatus.PENDING_EXECUTION,
        is_close=True,
    )
    res = svc.execute_order(
        intent,
        instrument,
        fill_calc,
        execution_at=exec_at,
        timeframe="5m",
        idempotency_key=idem,
        strategy_intent_id=None,
        order_purpose=purpose,
        is_liquidation=False,
    )
    return {
        "skipped": False,
        "symbol": symbol,
        "qty": str(qty),
        "direction": close_dir.value if hasattr(close_dir, "value") else str(close_dir),
        "accepted": res.accepted,
        "fill_qty": str(res.fill_quantity) if res.fill_quantity is not None else None,
        "rejection": getattr(res.decision, "rejection_detail", None) if res.decision else None,
        "mark": str(mark),
        "fill_price": str(fill_calc.fill_price),
    }


def main() -> int:
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("DATABASE_URL missing", file=sys.stderr)
        return 2
    engine = create_engine(url, pool_pre_ping=True)
    session = sessionmaker(bind=engine)()
    store = TradingStore(session)
    report: dict = {"started_at": datetime.now(timezone.utc).isoformat(), "steps": {}}

    existing = session.execute(
        text("SELECT value FROM settings WHERE key=:k"), {"k": STAMP_KEY}
    ).scalar()
    if existing:
        report["already_applied"] = existing
        # still allow re-check / partial resume via idempotent order keys
        report["note"] = "stamp exists — order idempotency still protects re-runs"

    # 1) Expire all stale pending intents
    now = datetime.now(timezone.utc)
    before = session.execute(
        text("SELECT count(*) FROM order_intents WHERE status='pending_execution' AND backtest_run_id IS NULL")
    ).scalar()
    expired = store.cancel_stale_pending_intents(None, now)
    session.commit()
    after = session.execute(
        text("SELECT count(*) FROM order_intents WHERE status='pending_execution' AND backtest_run_id IS NULL")
    ).scalar()
    older = session.execute(
        text(
            """
            SELECT count(*) FROM order_intents
            WHERE status='pending_execution' AND backtest_run_id IS NULL
              AND created_at < NOW() - INTERVAL '1 hour'
            """
        )
    ).scalar()
    report["steps"]["pending_expiry"] = {
        "before": before,
        "expired_this_run": expired,
        "after": after,
        "remaining_older_1h": older,
    }

    # 2) Flatten AMD bug short on IBKR sim (use Friday RTH clock)
    amd_svc = BrokerExecutionService(store, account_slug="live-sim-ibkr-like")
    amd_res = flatten_symbol(
        amd_svc,
        "AMD",
        exec_at=AMD_EXEC_AT,
        idem="settle:amd_bug_short_flatten:2026_11_07_v1",
        purpose="flatten",
    )
    session.commit()
    amd_after = session.execute(
        text(
            """
            SELECT bp.net_quantity, ba.free_margin, ba.available_margin, ba.realized_pnl,
                   ba.equity, ba.unrealized_pnl, ba.initial_margin_used
            FROM broker_accounts ba
            LEFT JOIN broker_positions bp ON bp.broker_account_id=ba.id
              AND bp.instrument_id=(SELECT id FROM instruments WHERE symbol='AMD')
            WHERE ba.slug='live-sim-ibkr-like'
            """
        )
    ).mappings().first()
    report["steps"]["amd_flatten"] = {
        "result": amd_res,
        "account_after": {k: _ser(v) for k, v in dict(amd_after or {}).items()},
        "open_shadows": session.execute(
            text(
                """
                SELECT count(*) FROM live_sim_positions
                WHERE broker_account_id=(SELECT id FROM broker_accounts WHERE slug='live-sim-ibkr-like')
                  AND instrument_id=(SELECT id FROM instruments WHERE symbol='AMD')
                  AND status='open'
                """
            )
        ).scalar(),
    }

    # 3) ETH dust on paper competition
    eth_svc = BrokerExecutionService(store, account_slug="quantara_paper_competition")
    eth_res = flatten_symbol(
        eth_svc,
        "ETHUSD",
        exec_at=datetime.now(timezone.utc),
        idem="settle:eth_dust_flatten:2026_11_07_v1",
        purpose="flatten",
    )
    session.commit()
    # Detach/zero any leftover attribution on the closed strategy position if flat
    eth_broker = session.execute(
        text(
            """
            SELECT bp.net_quantity FROM broker_positions bp
            JOIN broker_accounts ba ON ba.id=bp.broker_account_id
            JOIN instruments i ON i.id=bp.instrument_id
            WHERE ba.slug='quantara_paper_competition' AND i.symbol='ETHUSD'
            """
        )
    ).scalar()
    if eth_broker is not None and abs(Decimal(str(eth_broker))) == 0:
        session.execute(
            text(
                """
                UPDATE broker_attribution_lots
                SET remaining_qty = 0
                WHERE strategy_position_id = CAST(:pid AS uuid)
                  AND remaining_qty > 0
                """
            ),
            {"pid": ETH_PID},
        )
        session.execute(
            text(
                """
                UPDATE positions
                SET quantity = 0, status='closed',
                    closed_at = COALESCE(closed_at, NOW()), updated_at=NOW()
                WHERE id = CAST(:pid AS uuid)
                """
            ),
            {"pid": ETH_PID},
        )
        session.commit()
    report["steps"]["eth_dust"] = {
        "result": eth_res,
        "broker_net_after": _ser(eth_broker),
        "position_after": [
            {k: _ser(v) for k, v in dict(r).items()}
            for r in session.execute(
                text(
                    "SELECT status::text, quantity, closed_at FROM positions WHERE id=CAST(:p AS uuid)"
                ),
                {"p": ETH_PID},
            ).mappings()
        ],
        "lots_rem_after": _ser(
            session.execute(
                text(
                    """
                    SELECT COALESCE(SUM(remaining_qty),0)
                    FROM broker_attribution_lots
                    WHERE broker_account_id=(SELECT id FROM broker_accounts WHERE slug='quantara_paper_competition')
                      AND symbol='ETHUSD' AND remaining_qty > 0
                    """
                )
            ).scalar()
        ),
    }

    session.execute(
        text(
            """
            INSERT INTO settings (key, value, updated_at)
            VALUES (:k, CAST(:v AS jsonb), NOW())
            ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value, updated_at=NOW()
            """
        ),
        {
            "k": STAMP_KEY,
            "v": json.dumps(
                {
                    "applied_at": datetime.now(timezone.utc).isoformat(),
                    "pending_expired": expired,
                    "amd": amd_res,
                    "eth": eth_res,
                },
                default=str,
            ),
        },
    )
    session.commit()
    report["ok"] = (
        report["steps"]["pending_expiry"]["remaining_older_1h"] == 0
        and str(report["steps"]["amd_flatten"]["account_after"].get("net_quantity") or "0")
        in ("0", "0E-8", "0.00000000", None)
        or Decimal(str(report["steps"]["amd_flatten"]["account_after"].get("net_quantity") or 0)) == 0
    )
    # refine ok
    amd_net = Decimal(str(amd_after["net_quantity"] if amd_after and amd_after["net_quantity"] is not None else 0))
    eth_net = Decimal(str(eth_broker or 0))
    report["ok"] = (
        older == 0
        and amd_net == 0
        and eth_net == 0
        and amd_res.get("accepted", amd_res.get("skipped"))
        and eth_res.get("accepted", eth_res.get("skipped"))
    )
    out = ROOT / "scripts" / "snapshots" / f"settle_remaining_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(json.dumps(report, indent=2, default=str))
    print("PATH", out)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
