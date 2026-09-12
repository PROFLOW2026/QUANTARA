#!/usr/bin/env python3
"""LEGACY — Supabase archive verification only. Runtime uses verify_local_runtime.py."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "apps" / "engine"
sys.path.insert(0, str(ENGINE))

from sqlalchemy import text  # noqa: E402

from quantara_engine.core.config import settings  # noqa: E402
from quantara_engine.db.session import engine, session_scope  # noqa: E402
from quantara_engine.domain.types import Mode  # noqa: E402
from quantara_engine.persistence.store import TradingStore  # noqa: E402


def _fail(msg: str) -> int:
    print(f"FAIL: {msg}")
    return 1


def main() -> int:
    print("QUANTARA LEGACY Supabase archive verification (NOT runtime)")
    print("=" * 50)

    if not (ROOT / ".env").exists():
        return _fail("Repo root .env missing - copy .env.example to .env")

    if not settings.database_configured:
        return _fail(
            "DATABASE_URL is empty in .env - set Supabase pooler URI for QUANTARA project"
        )

    # 1. Connection
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        print("PASS  Database connected")
    except Exception as exc:
        return _fail(f"Database connection — {exc}")

    # 2. Schema parity script
    parity = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "verify_schema_parity.py")],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    if parity.returncode != 0:
        print(parity.stdout)
        print(parity.stderr)
        return _fail("Schema parity script failed")
    print(f"PASS  Schema parity ({parity.stdout.strip()})")

    # 3. Seed (idempotent)
    seed = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "seed.py")],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    print(seed.stdout)
    if seed.returncode != 0:
        print(seed.stderr)
        return _fail("Seed failed")
    print("PASS  Seed")

    # 4. Reference data checks
    instrument_id: str | None = None
    portfolio_id: str | None = None
    instance_id: str | None = None

    with session_scope() as session:
        store = TradingStore(session)
        instrument = store.get_instrument_by_symbol("XAUUSD")
        if not instrument:
            return _fail("XAUUSD missing after seed")
        instrument_id = instrument.id
        portfolio = store.resolve_paper_portfolio("competition")
        portfolio_id = portfolio.id
        entries = store.list_competition_entries()
        if not entries:
            return _fail("Competition portfolios missing after seed_competition")
        instance = entries[0]["instance"]
        instance_id = instance.id
        print(f"PASS  Reference data (XAUUSD, paper portfolio, instance {instance.id[:8]}...)")

    # 5. Demo pipeline
    demo = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "demo.py")],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    print(demo.stdout)
    if demo.returncode != 0:
        print(demo.stderr)
        return _fail("Demo pipeline failed")
    print("PASS  Demo pipeline")

    # 6. Persisted entity counts
    assert instrument_id and portfolio_id and instance_id

    with session_scope() as session:
        store = TradingStore(session)
        candles = store.list_candles(instrument_id, "1h", limit=5)
        decisions = store.list_decisions(instance_id, limit=5)
        trades = store.list_trades(portfolio_id, limit=5, paper_only=True)
        backtests = store.list_backtests(limit=3)
        signals = session.execute(
            text("SELECT COUNT(*) FROM signals WHERE mode = 'paper'")
        ).scalar()

        if not candles:
            return _fail("No candles persisted")
        if not backtests:
            return _fail("No backtest_runs persisted")
        print(
            f"PASS  Persistence counts — candles≥1, decisions≥{len(decisions)}, "
            f"paper_trades≥{len(trades)}, backtests≥{len(backtests)}, paper_signals={signals}"
        )

        state = store.load_portfolio_state(portfolio_id)
        trade_count = len(state.trades)

    with session_scope() as session:
        store = TradingStore(session)
        reloaded = store.load_portfolio_state(portfolio_id)
        if len(reloaded.trades) != trade_count:
            return _fail("Restart reload trade count mismatch")
        paper_decisions = store.count_decisions_today(instance_id, mode=Mode.PAPER)
        print(
            f"PASS  Restart persistence (trades={trade_count}, paper_decisions_today={paper_decisions})"
        )

    # 7. Restart pytest
    restart = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/test_persistence_restart.py",
            "-v",
            "--tb=short",
        ],
        cwd=ENGINE,
        capture_output=True,
        text=True,
    )
    print(restart.stdout)
    if restart.returncode != 0:
        print(restart.stderr)
        return _fail("test_persistence_restart failed")
    print("PASS  Restart pytest")

    print("=" * 50)
    print("ALL CHECKS PASSED — Supabase runtime verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
