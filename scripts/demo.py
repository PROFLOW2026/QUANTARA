#!/usr/bin/env python3
"""End-to-end demo: mock candles → backtest → paper pipeline."""

from __future__ import annotations

import sys
import uuid
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "apps" / "engine"
sys.path.insert(0, str(ENGINE))

from quantara_engine.backtesting.runner import BacktestRun, BacktestRunner  # noqa: E402
from quantara_engine.db.session import session_scope  # noqa: E402
from quantara_engine.domain.types import ExecutionAssumptions, Mode  # noqa: E402
from quantara_engine.market_data.adapters.mock import MockMarketDataProvider  # noqa: E402
from quantara_engine.persistence.store import TradingStore  # noqa: E402
from quantara_workers.jobs.run_strategy import OWNER_ID, run_strategy_job  # noqa: E402


def main() -> int:
    print("QUANTARA Demo — starting...")

    with session_scope() as session:
        store = TradingStore(session)

        portfolio = store.get_or_create_paper_portfolio(
            owner_id=OWNER_ID,
            initial_capital=Decimal("10000"),
        )
        instance = store.get_paper_strategy_instance(portfolio.id)
        if not instance:
            print("  No strategy instance — run scripts/seed.py first")
            return 1

        instrument = store.get_instrument_by_symbol("XAUUSD")
        if not instrument:
            print("  XAUUSD not found — run scripts/seed.py first")
            return 1

        risk_profile = store.get_risk_profile_by_slug("balanced")
        if not risk_profile:
            print("  balanced risk profile not found — run scripts/seed.py first")
            return 1

        provider = MockMarketDataProvider()
        candles = provider.generate_candles(instrument.id, "1h", 250)
        for candle in candles:
            store.upsert_candle(candle)
        store.flush()
        print(f"  Loaded {len(candles)} mock XAU/USD 1h candles")

        bt = BacktestRun(
            id=str(uuid.uuid4()),
            strategy_instance=instance,
            instrument=instrument,
            risk_profile=risk_profile,
            candles=candles,
            initial_capital=Decimal("10000"),
            execution_assumptions=ExecutionAssumptions(),
        )
        bt = BacktestRunner().run(bt, store=store)
        print(
            f"  Backtest: status={bt.status.value}, trades={len(bt.trades)}"
        )
        if bt.metrics:
            print(
                f"    return={bt.metrics.get('total_return_pct', 0):.2f}%  "
                f"fingerprint={bt.dataset_fingerprint[:16]}..."
            )

        run_strategy_job(store=store)
        state = store.load_portfolio_state(portfolio.id)
        decisions = store.count_decisions_today(instance.id, mode=Mode.PAPER)
        trades = len(state.trades)
        print(f"  Paper pipeline: decisions_today={decisions}, trades={trades}")
        print(f"    equity={state.portfolio.equity}  balance={state.portfolio.balance}")

    print("QUANTARA Demo — complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
