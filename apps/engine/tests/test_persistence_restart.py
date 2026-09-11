"""Verify portfolio state survives session restart (PostgreSQL persistence)."""

from __future__ import annotations

import sys
import uuid
from decimal import Decimal
from pathlib import Path

import pytest

ENGINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ENGINE))

from quantara_engine.backtesting.runner import BacktestRun, BacktestRunner  # noqa: E402
from quantara_engine.core.config import settings  # noqa: E402
from quantara_engine.db.session import session_scope  # noqa: E402
from quantara_engine.domain.types import ExecutionAssumptions, Mode, StrategyInstance  # noqa: E402
from quantara_engine.market_data.adapters.mock import MockMarketDataProvider  # noqa: E402
from quantara_engine.persistence.store import TradingStore  # noqa: E402
from quantara_workers.jobs.run_strategy import OWNER_ID, run_strategy_job  # noqa: E402


@pytest.mark.skipif(
    not settings.database_configured,
    reason="Set DATABASE_URL in repo root .env (Supabase QUANTARA pooler URI)",
)
def test_persistence_restart_counts_match() -> None:
    """Run demo pipeline, then reload state in a fresh session and compare counts."""
    portfolio_id: str | None = None
    expected_trades = 0
    expected_decisions = 0
    instance_id: str | None = None

    with session_scope() as session:
        store = TradingStore(session)

        portfolio = store.get_or_create_paper_portfolio(
            owner_id=OWNER_ID,
            initial_capital=Decimal("10000"),
        )
        portfolio_id = portfolio.id
        instrument = store.get_instrument_by_symbol("XAUUSD")
        assert instrument is not None

        risk_profile = store.get_risk_profile_by_slug("balanced")
        assert risk_profile is not None

        instance = store.get_paper_strategy_instance(portfolio.id)
        if instance is None:
            sv = store.get_strategy_version("gold-trend-pullback", "v1")
            if sv is None:
                pytest.skip("Strategy version gold-trend-pullback/v1 not seeded")
            instance = StrategyInstance(
                id=str(uuid.uuid4()),
                portfolio_id=portfolio.id,
                strategy_version_id=str(sv["id"]),
                strategy_slug="gold-trend-pullback",
                strategy_version="v1",
                instrument_id=instrument.id,
                timeframe="1h",
                risk_profile_id=risk_profile.id,
            )
        instance_id = instance.id

        provider = MockMarketDataProvider()
        candles = provider.generate_candles(instrument.id, "1h", 250)
        for candle in candles:
            store.upsert_candle(candle)
        store.flush()

        bt = BacktestRun(
            id=str(uuid.uuid4()),
            strategy_instance=instance,
            instrument=instrument,
            risk_profile=risk_profile,
            candles=candles,
            initial_capital=Decimal("10000"),
            execution_assumptions=ExecutionAssumptions(),
        )
        BacktestRunner().run(bt, store=store)
        run_strategy_job(store=store)

        state = store.load_portfolio_state(portfolio.id)
        expected_trades = len(state.trades)
        expected_decisions = store.count_decisions_today(instance.id, mode=Mode.PAPER)

    assert portfolio_id is not None
    assert instance_id is not None

    with session_scope() as session:
        store = TradingStore(session)
        reloaded = store.load_portfolio_state(portfolio_id)

        assert len(reloaded.trades) == expected_trades
        assert (
            store.count_decisions_today(instance_id, mode=Mode.PAPER)
            == expected_decisions
        )
        assert reloaded.portfolio.balance == reloaded.portfolio.balance.quantize(Decimal("0.01"))
