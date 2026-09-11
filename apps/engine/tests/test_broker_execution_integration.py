"""Real-DB broker execution integration tests (disposable PostgreSQL + migration 0006)."""

from __future__ import annotations

import sys
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import text

from quantara_engine.broker.attribution import attributed_remaining_quantity
from quantara_engine.broker.execution_bridge import execute_through_broker
from quantara_engine.broker.execution_service import BrokerExecutionService
from quantara_engine.domain.types import Direction, IntentStatus, OrderIntent
from quantara_engine.execution.fill_calculator import FillResult
from quantara_engine.persistence.store import TradingStore

from tests.broker_integration_support import (
    STARTING_CASH,
    read_account_balances,
    setup_active_test_account,
    setup_inactive_placeholder_account,
    patch_paper_account_slug,
)

pytestmark = pytest.mark.usefixtures("broker_test_database", "broker_fresh_market_gate")


@pytest.fixture
def broker_fresh_market_gate(monkeypatch):
    """Integration tests supply synthetic fills — bypass live candle freshness gates."""

    def _fresh(*_args, **_kwargs):
        return True, None

    def _open(*_args, **_kwargs):
        return True

    monkeypatch.setattr(
        "quantara_engine.broker.execution_service.data_fresh_for_instrument",
        _fresh,
    )
    monkeypatch.setattr(
        "quantara_engine.broker.execution_service.market_open_for_instrument",
        _open,
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


def _fill(price: Decimal = Decimal("100"), fees: Decimal = Decimal("1")) -> FillResult:
    return FillResult(
        fill_price=price,
        base_price=price,
        spread_cost=Decimal("0.05"),
        slippage=Decimal("0"),
        fees=fees,
    )


def test_profit_fill_persists_balance_and_net_realized(monkeypatch):
    patch_paper_account_slug(monkeypatch)
    from quantara_engine.db.session import SessionLocal

    session = SessionLocal()
    try:
        store = TradingStore(session)
        account_id = setup_active_test_account(store)
        instrument = store.get_instrument_by_symbol("NVDA")
        assert instrument
        svc = BrokerExecutionService(store)
        pid = str(uuid.uuid4())
        spid = str(uuid.uuid4())

        entry = svc.execute_order(
            _intent(qty=Decimal("10"), direction=Direction.LONG, portfolio_id=pid),
            instrument,
            _fill(Decimal("100")),
            execution_at=datetime.now(timezone.utc),
            timeframe="5m",
            idempotency_key="itest:entry:1",
            strategy_position_id=spid,
        )
        assert entry.accepted, entry.decision
        exit_res = svc.execute_order(
            _intent(qty=Decimal("10"), direction=Direction.SHORT, portfolio_id=pid, is_close=True),
            instrument,
            _fill(Decimal("110")),
            execution_at=datetime.now(timezone.utc),
            timeframe="5m",
            idempotency_key="itest:exit:1",
            order_purpose="close",
            strategy_position_id=spid,
        )
        assert exit_res.accepted, exit_res.decision
        session.flush()

        bal = read_account_balances(store, account_id)
        assert bal["balance"] == STARTING_CASH + Decimal("98")
        assert bal["realized_pnl"] == Decimal("98")
        assert bal["gross_realized_pnl"] == Decimal("100")
        assert bal["fees_paid"] == Decimal("2")
        assert bal["cash"] == STARTING_CASH + Decimal("98")
    finally:
        session.rollback()
        session.close()


def test_fee_not_double_spread(monkeypatch):
    patch_paper_account_slug(monkeypatch)
    from quantara_engine.db.session import SessionLocal

    session = SessionLocal()
    try:
        store = TradingStore(session)
        account_id = setup_active_test_account(store)
        instrument = store.get_instrument_by_symbol("NVDA")
        assert instrument
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


def test_idempotent_retry_returns_same_fill(monkeypatch):
    patch_paper_account_slug(monkeypatch)
    from quantara_engine.db.session import SessionLocal

    session = SessionLocal()
    try:
        store = TradingStore(session)
        setup_active_test_account(store)
        instrument = store.get_instrument_by_symbol("NVDA")
        assert instrument
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
        session.flush()
    finally:
        session.rollback()
        session.close()


def test_inactive_placeholder_account_rejects_execution(monkeypatch):
    from quantara_engine.db.session import SessionLocal

    session = SessionLocal()
    try:
        store = TradingStore(session)
        setup_inactive_placeholder_account(store)
        instrument = store.get_instrument_by_symbol("NVDA")
        assert instrument
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
        session.flush()
    finally:
        session.rollback()
        session.close()


def test_netting_shadow_exit_no_broker_order(monkeypatch):
    patch_paper_account_slug(monkeypatch)
    from quantara_engine.db.session import SessionLocal

    session = SessionLocal()
    try:
        store = TradingStore(session)
        account_id = setup_active_test_account(store)
        instrument = store.get_instrument_by_symbol("NVDA")
        assert instrument
        svc = BrokerExecutionService(store)
        pid_a = str(uuid.uuid4())
        pid_b = str(uuid.uuid4())
        spid_a = str(uuid.uuid4())
        spid_b = str(uuid.uuid4())
        at = datetime.now(timezone.utc)

        r_a = svc.execute_order(
            _intent(qty=Decimal("100"), direction=Direction.LONG, portfolio_id=pid_a),
            instrument,
            _fill(Decimal("100"), Decimal("0")),
            execution_at=at,
            timeframe="5m",
            idempotency_key="itest:net:a",
            strategy_position_id=spid_a,
        )
        r_b = svc.execute_order(
            _intent(qty=Decimal("100"), direction=Direction.SHORT, portfolio_id=pid_b),
            instrument,
            _fill(Decimal("100"), Decimal("0")),
            execution_at=at,
            timeframe="5m",
            idempotency_key="itest:net:b",
            strategy_position_id=spid_b,
        )
        assert r_a.physical_opened_qty == Decimal("100")
        assert r_b.physical_opened_qty == Decimal("0")

        orders_before = int(
            store.session.execute(text("SELECT COUNT(*) FROM broker_orders")).scalar() or 0
        )
        shadow = execute_through_broker(
            store,
            portfolio_id=pid_b,
            instrument=instrument,
            direction=Direction.LONG,
            quantity=Decimal("100"),
            fill=_fill(Decimal("101"), Decimal("0")),
            execution_at=at,
            timeframe="5m",
            idempotency_key="itest:shadow:exit:b",
            is_close=True,
            strategy_position_id=spid_b,
            skip_if_not_competition=False,
        )
        orders_after = int(
            store.session.execute(text("SELECT COUNT(*) FROM broker_orders")).scalar() or 0
        )
        assert shadow is not None
        assert shadow.shadow_only
        assert orders_after == orders_before

        attr_a = attributed_remaining_quantity(
            store, broker_account_id=account_id, symbol="NVDA", strategy_position_id=spid_a
        )
        attr_b = attributed_remaining_quantity(
            store, broker_account_id=account_id, symbol="NVDA", strategy_position_id=spid_b
        )
        assert attr_a == Decimal("0")
        assert attr_b == Decimal("0")
        session.flush()
    finally:
        session.rollback()
        session.close()


def test_partial_netting_physical_attribution(monkeypatch):
    patch_paper_account_slug(monkeypatch)
    from quantara_engine.db.session import SessionLocal

    session = SessionLocal()
    try:
        store = TradingStore(session)
        account_id = setup_active_test_account(store)
        instrument = store.get_instrument_by_symbol("NVDA")
        assert instrument
        svc = BrokerExecutionService(store)
        pid_a = str(uuid.uuid4())
        pid_b = str(uuid.uuid4())
        spid_a = str(uuid.uuid4())
        at = datetime.now(timezone.utc)

        svc.execute_order(
            _intent(qty=Decimal("100"), direction=Direction.LONG, portfolio_id=pid_a),
            instrument,
            _fill(Decimal("100"), Decimal("0")),
            execution_at=at,
            timeframe="5m",
            idempotency_key="itest:partial:a",
            strategy_position_id=spid_a,
        )
        svc.execute_order(
            _intent(qty=Decimal("60"), direction=Direction.SHORT, portfolio_id=pid_b),
            instrument,
            _fill(Decimal("100"), Decimal("0")),
            execution_at=at,
            timeframe="5m",
            idempotency_key="itest:partial:b",
        )
        attr_a = attributed_remaining_quantity(
            store, broker_account_id=account_id, symbol="NVDA", strategy_position_id=spid_a
        )
        pos_qty = store.session.execute(
            text(
                """
                SELECT net_quantity FROM broker_positions bp
                JOIN broker_accounts ba ON ba.id = bp.broker_account_id
                WHERE ba.slug = :slug
                """
            ),
            {"slug": "__test_broker_integration__"},
        ).scalar()
        assert attr_a == Decimal("40")
        assert Decimal(str(pos_qty)) == Decimal("40")
        session.flush()
    finally:
        session.rollback()
        session.close()


def test_mark_to_market_persists(monkeypatch):
    patch_paper_account_slug(monkeypatch)
    from quantara_engine.db.session import SessionLocal

    session = SessionLocal()
    try:
        store = TradingStore(session)
        setup_active_test_account(store)
        instrument = store.get_instrument_by_symbol("NVDA")
        assert instrument
        svc = BrokerExecutionService(store)
        svc.execute_order(
            _intent(qty=Decimal("10"), direction=Direction.LONG, portfolio_id=str(uuid.uuid4())),
            instrument,
            _fill(Decimal("100"), Decimal("0")),
            execution_at=datetime.now(timezone.utc),
            timeframe="5m",
            idempotency_key="itest:mtm:entry",
        )
        svc.mark_to_market({"NVDA": Decimal("110")})
        session.flush()
        snap = svc.load_account_snapshot()
        assert snap.unrealized_pnl == Decimal("100")
        session.flush()
    finally:
        session.rollback()
        session.close()


def test_reset_dry_run_and_execute_on_test_db(monkeypatch):
    from quantara_engine.db.session import SessionLocal
    from tests.broker_integration_support import TEST_ACCOUNT_SLUG, setup_active_test_account

    root = Path(__file__).resolve().parents[3]
    sys.path.insert(0, str(root / "apps" / "engine"))
    sys.path.insert(0, str(root / "scripts"))
    import paper_broker_reset as reset_mod

    monkeypatch.setattr(reset_mod, "ACCOUNT_SLUG", TEST_ACCOUNT_SLUG)

    session = SessionLocal()
    try:
        store = TradingStore(session)
        setup_active_test_account(store)
        session.flush()
        before = reset_mod._counts(store)
        assert before["reference_portfolios"] == 160
        reset_mod._print_dry_run_plan(before)
        reset_mod._execute_reset(store)
        session.flush()
        after = reset_mod._counts(store)
        assert after.get("broker_orders", -1) == 0
        assert after.get("broker_positions", -1) == 0
        row = store.session.execute(
            text(
                """
                SELECT cash, balance, equity, realized_pnl, gross_realized_pnl, fees_paid
                FROM broker_accounts WHERE slug = :slug
                """
            ),
            {"slug": TEST_ACCOUNT_SLUG},
        ).mappings().first()
        assert row
        assert Decimal(str(row["cash"])) == STARTING_CASH
        assert Decimal(str(row["balance"])) == STARTING_CASH
        assert Decimal(str(row["realized_pnl"])) == Decimal("0")
        session.flush()
    finally:
        session.rollback()
        session.close()
