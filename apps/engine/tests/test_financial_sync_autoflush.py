"""Regression: canonical sync must see unflushed trades when autoflush=False."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock, call

import pytest

from quantara_engine.domain.types import (
    Direction,
    ExitReason,
    Mode,
    Portfolio,
    PortfolioStatus,
    Trade,
    new_id,
)
from quantara_engine.persistence.store import TradingStore
from quantara_engine.portfolio.balance_reconciliation import (
    apply_canonical_financial_state,
    reconcile_portfolio_financial_state,
)


def _portfolio() -> Portfolio:
    return Portfolio(
        id="p1",
        name="Test",
        mode=Mode.PAPER,
        initial_capital=Decimal("2000"),
        balance=Decimal("2000"),
        equity=Decimal("2000"),
        status=PortfolioStatus.ACTIVE,
    )


def _store_with_autoflush_off_session() -> tuple[TradingStore, MagicMock]:
    session = MagicMock()
    session.autoflush = False
    return TradingStore(session), session


def test_unflushed_trades_invisible_until_explicit_flush():
    """Root cause: SQL SUM cannot see pending session trades until flush."""
    store, session = _store_with_autoflush_off_session()
    portfolio = _portfolio()
    visible = {"flushed": False}

    def _sum_realized(portfolio_ids):
        if not visible["flushed"]:
            return {}
        return {portfolio.id: Decimal("25.50")}

    store.sum_realized_pnl_for_portfolio_ids = _sum_realized
    # Trades persisted in session (save_trade merge) but not flushed — invisible to SQL SUM.
    assert store.sum_realized_pnl_for_portfolio_ids([portfolio.id]).get(portfolio.id, Decimal("0")) == Decimal("0")

    def _mark_visible():
        visible["flushed"] = True

    session.flush.side_effect = _mark_visible
    session.flush()
    assert store.sum_realized_pnl_for_portfolio_ids([portfolio.id])[portfolio.id] == Decimal("25.50")


def test_sync_flushes_before_ledger_sum():
    store, session = _store_with_autoflush_off_session()
    portfolio = _portfolio()
    call_order: list[str] = []

    session.flush.side_effect = lambda: call_order.append("flush")
    store.sum_realized_pnl_for_portfolio_ids = lambda ids: (
        call_order.append("sum_realized"),
        {portfolio.id: Decimal("25.50")},
    )[1]
    store.sum_open_position_financials_for_portfolio_ids = lambda ids: (
        call_order.append("sum_unrealized"),
        {},
    )[1]
    store.update_portfolios_financial_canonical_batch = lambda portfolios: call_order.append("persist")

    store.sync_portfolios_financial_state_from_ledger([portfolio], flush=False)

    assert call_order == ["flush", "sum_realized", "sum_unrealized", "persist"]
    assert portfolio.balance == Decimal("2025.50")
    assert portfolio.unrealized_pnl == Decimal("0.00")
    assert portfolio.equity == Decimal("2025.50")


def test_sync_includes_unflushed_trades_when_autoflush_false():
    """Would fail old code that summed realized before flush."""
    store, session = _store_with_autoflush_off_session()
    portfolio = _portfolio()
    ledger_visible = False

    def _flush():
        nonlocal ledger_visible
        ledger_visible = True

    session.flush.side_effect = _flush

    def _sum_realized(portfolio_ids):
        if not ledger_visible:
            return {}
        return {portfolio.id: Decimal("25.50")}

    store.sum_realized_pnl_for_portfolio_ids = _sum_realized
    store.sum_open_position_financials_for_portfolio_ids = lambda ids: {}
    store.update_portfolios_financial_canonical_batch = lambda portfolios: None

    store.sync_portfolios_financial_state_from_ledger([portfolio], flush=True)

    assert session.flush.call_count >= 1
    assert portfolio.balance == Decimal("2025.50")
    assert portfolio.equity == Decimal("2025.50")
    recon = reconcile_portfolio_financial_state(portfolio, Decimal("25.50"), Decimal("0"))
    assert recon.balance_difference == Decimal("0.00")
    assert recon.equity_difference == Decimal("0.00")


def test_sync_zeroes_ghost_unrealized_when_no_open_positions():
    store, session = _store_with_autoflush_off_session()
    portfolio = _portfolio()
    portfolio.unrealized_pnl = Decimal("42.00")
    portfolio.equity = Decimal("2042.00")

    store.sum_realized_pnl_for_portfolio_ids = lambda ids: {portfolio.id: Decimal("5.00")}
    store.sum_open_position_financials_for_portfolio_ids = lambda ids: {}
    store.update_portfolios_financial_canonical_batch = lambda portfolios: None

    store.sync_portfolios_financial_state_from_ledger([portfolio], flush=True)

    assert portfolio.unrealized_pnl == Decimal("0.00")
    assert portfolio.exposure_notional == Decimal("0.00")
    assert portfolio.reserved_capital == Decimal("0.00")
    assert portfolio.equity == Decimal("2005.00")


def test_sync_matches_open_position_unrealized():
    store, session = _store_with_autoflush_off_session()
    portfolio = _portfolio()
    portfolio.unrealized_pnl = Decimal("99.00")

    store.sum_realized_pnl_for_portfolio_ids = lambda ids: {portfolio.id: Decimal("0")}
    store.sum_open_position_financials_for_portfolio_ids = lambda ids: {
        portfolio.id: (Decimal("10.00"), Decimal("110.00"))
    }
    store.update_portfolios_financial_canonical_batch = lambda portfolios: None

    store.sync_portfolios_financial_state_from_ledger([portfolio], flush=True)

    assert portfolio.unrealized_pnl == Decimal("10.00")
    assert portfolio.exposure_notional == Decimal("110.00")
    assert portfolio.equity == Decimal("2010.00")


def test_old_balance_only_sync_alias_delegates_to_full_sync():
    store, session = _store_with_autoflush_off_session()
    portfolio = _portfolio()
    calls: list[str] = []

    store.sync_portfolios_financial_state_from_ledger = lambda portfolios, flush=False: calls.append("full")

    store.sync_portfolios_balance_from_ledger([portfolio], flush=True)

    assert calls == ["full"]
