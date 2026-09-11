"""Real-DB broker execution integration tests."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import text

from quantara_engine.broker.execution_service import BrokerExecutionService
from quantara_engine.domain.types import Direction, IntentStatus, OrderIntent
from quantara_engine.execution.fill_calculator import FillResult
from quantara_engine.persistence.store import TradingStore

from tests.broker_integration_support import (
    STARTING_CASH,
    read_account_balances,
    requires_broker_db,
    setup_active_test_account,
    patch_paper_account_slug,
)


def _intent(*, qty: Decimal, direction: Direction, portfolio_id: str, is_close: bool = False):
    return OrderIntent(
        id=str(uuid.uuid4()),
        signal_id="",
        strategy_instance_id="",
        portfolio_id=portfolio_id,
        direction=direction,
        quantity=qty,
        stop_loss=Decimal("0"),
        take_profit=None,
        target_risk_amount=Decimal("0"),
        actual_risk_amount=Decimal("0"),
        signal_candle_timestamp=datetime.now(timezone.utc),
        risk_profile_id="",
        status=IntentStatus.PENDING_EXECUTION,
        is_close=is_close,
    )


@requires_broker_db
def test_profit_fill_persists_balance(monkeypatch):
    patch_paper_account_slug(monkeypatch)
    from quantara_engine.db.session import SessionLocal

    session = SessionLocal()
    try:
        store = TradingStore(session)
        account_id = setup_active_test_account(store)
        inst_row = store.session.execute(
            text("SELECT id::text, symbol FROM instruments WHERE symbol = 'NVDA' LIMIT 1")
        ).mappings().first()
        if not inst_row:
            pytest.skip("NVDA instrument missing")
        instrument = store.get_instrument_by_symbol("NVDA")
        svc = BrokerExecutionService(store)
        pid = str(uuid.uuid4())
        spid = str(uuid.uuid4())

        entry_fill = FillResult(
            fill_price=Decimal("100"),
            base_price=Decimal("100"),
            spread_cost=Decimal("0.05"),
            slippage=Decimal("0"),
            fees=Decimal("1"),
        )
        svc.execute_order(
            _intent(qty=Decimal("10"), direction=Direction.LONG, portfolio_id=pid),
            instrument,
            entry_fill,
            execution_at=datetime.now(timezone.utc),
            timeframe="5m",
            idempotency_key="itest:entry:1",
            strategy_position_id=spid,
        )

        exit_fill = FillResult(
            fill_price=Decimal("110"),
            base_price=Decimal("110"),
            spread_cost=Decimal("0.05"),
            slippage=Decimal("0"),
            fees=Decimal("1"),
        )
        svc.execute_order(
            _intent(qty=Decimal("10"), direction=Direction.SHORT, portfolio_id=pid, is_close=True),
            instrument,
            exit_fill,
            execution_at=datetime.now(timezone.utc),
            timeframe="5m",
            idempotency_key="itest:exit:1",
            order_purpose="close",
            strategy_position_id=spid,
        )
        session.flush()

        bal = read_account_balances(store, account_id)
        # +100 realized, -2 fees
        assert bal["balance"] == STARTING_CASH + Decimal("98")
        assert bal["realized_pnl"] == Decimal("100")
        assert bal["cash"] == STARTING_CASH + Decimal("98")
    finally:
        session.rollback()
        session.close()


@requires_broker_db
def test_fee_not_double_spread(monkeypatch):
    patch_paper_account_slug(monkeypatch)
    from quantara_engine.db.session import SessionLocal

    session = SessionLocal()
    try:
        store = TradingStore(session)
        account_id = setup_active_test_account(store)
        instrument = store.get_instrument_by_symbol("NVDA")
        if not instrument:
            pytest.skip("NVDA missing")
        svc = BrokerExecutionService(store)
        pid = str(uuid.uuid4())
        fill = FillResult(
            fill_price=Decimal("170"),
            base_price=Decimal("169.90"),
            spread_cost=Decimal("0.10"),
            slippage=Decimal("0"),
            fees=Decimal("2"),
        )
        svc.execute_order(
            _intent(qty=Decimal("5"), direction=Direction.LONG, portfolio_id=pid),
            instrument,
            fill,
            execution_at=datetime.now(timezone.utc),
            timeframe="5m",
            idempotency_key="itest:fee:1",
        )
        session.flush()
        bal = read_account_balances(store, account_id)
        assert bal["balance"] == STARTING_CASH - Decimal("2")
    finally:
        session.rollback()
        session.close()


@requires_broker_db
def test_idempotent_retry_returns_same_fill(monkeypatch):
    patch_paper_account_slug(monkeypatch)
    from quantara_engine.db.session import SessionLocal

    session = SessionLocal()
    try:
        store = TradingStore(session)
        setup_active_test_account(store)
        instrument = store.get_instrument_by_symbol("NVDA")
        if not instrument:
            pytest.skip("NVDA missing")
        svc = BrokerExecutionService(store)
        pid = str(uuid.uuid4())
        fill = FillResult(
            fill_price=Decimal("175.50"),
            base_price=Decimal("175"),
            spread_cost=Decimal("0.25"),
            slippage=Decimal("0.25"),
            fees=Decimal("1.5"),
        )
        key = "itest:idem:1"
        r1 = svc.execute_order(
            _intent(qty=Decimal("3"), direction=Direction.LONG, portfolio_id=pid),
            instrument,
            fill,
            execution_at=datetime.now(timezone.utc),
            timeframe="5m",
            idempotency_key=key,
        )
        r2 = svc.execute_order(
            _intent(qty=Decimal("3"), direction=Direction.LONG, portfolio_id=pid),
            instrument,
            fill,
            execution_at=datetime.now(timezone.utc),
            timeframe="5m",
            idempotency_key=key,
        )
        assert r1.accepted and r2.accepted
        assert r1.broker_fill_id == r2.broker_fill_id
        assert r1.fill_price == r2.fill_price == Decimal("175.50")
        assert r2.from_existing_fill
    finally:
        session.rollback()
        session.close()


@requires_broker_db
def test_migration_placeholder_rejects_execution(monkeypatch):
    from quantara_engine.db.session import SessionLocal

    session = SessionLocal()
    try:
        store = TradingStore(session)
        row = store.session.execute(
            text(
                """
                SELECT id::text FROM broker_accounts
                WHERE slug = 'quantara_paper_competition' AND is_active = FALSE
                LIMIT 1
                """
            )
        ).scalar()
        if not row:
            pytest.skip("placeholder account not present")
        instrument = store.get_instrument_by_symbol("NVDA")
        if not instrument:
            pytest.skip("NVDA missing")
        svc = BrokerExecutionService(store)
        res = svc.execute_order(
            _intent(qty=Decimal("1"), direction=Direction.LONG, portfolio_id=str(uuid.uuid4())),
            instrument,
            FillResult(Decimal("100"), Decimal("100"), Decimal("0"), Decimal("0"), Decimal("0")),
            execution_at=datetime.now(timezone.utc),
            timeframe="5m",
            idempotency_key="itest:paused:1",
        )
        assert not res.accepted
    finally:
        session.rollback()
        session.close()
