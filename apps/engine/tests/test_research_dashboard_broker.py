"""Research dashboard broker account snapshot — regression for AccountState UnboundLocalError."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from quantara_engine.broker.accounts import RESEARCH_PAPER_ACCOUNT_SLUG
from quantara_engine.broker.execution_service import BrokerExecutionService
from quantara_engine.broker.types import AccountState


def test_load_account_snapshot_without_research_replay_isolation():
    store = MagicMock()
    store.research_replay_isolation = False
    svc = BrokerExecutionService(store, account_slug=RESEARCH_PAPER_ACCOUNT_SLUG)
    row = {
        "id": "acct-1",
        "cash": Decimal("320000"),
        "balance": Decimal("318000"),
        "realized_pnl": Decimal("1200"),
        "spot_crypto_cash": Decimal("0"),
        "account_state": AccountState.ACTIVE.value,
        "is_active": True,
        "pending_owner_reset": False,
    }
    with patch.object(svc, "get_account_row", return_value=row):
        store.session.execute.return_value.mappings.return_value.all.return_value = []
        snap = svc.load_account_snapshot()

    assert snap.equity is not None
    assert snap.cash == Decimal("320000")
    assert snap.account_state == AccountState.ACTIVE


def test_broker_account_summary_route_returns_equity_fields():
    from quantara_engine.api.routes import broker_account_summary

    store = MagicMock()
    account = MagicMock()
    account.profile_slug = RESEARCH_PAPER_ACCOUNT_SLUG
    account.account_state = AccountState.ACTIVE
    account.cash = Decimal("320000")
    account.balance = Decimal("319000")
    account.equity = Decimal("321500")
    account.realized_pnl = Decimal("1500")
    account.unrealized_pnl = Decimal("2500")
    account.available_margin = Decimal("300000")
    account.spot_crypto_cash = Decimal("0")
    account.initial_margin_used = Decimal("0")
    account.maintenance_margin_required = Decimal("0")
    account.free_margin = Decimal("300000")
    account.margin_level_pct = None
    account.gross_exposure = Decimal("10000")
    account.net_exposure = Decimal("10000")
    account.gross_leverage = Decimal("0.03")
    account.net_leverage = Decimal("0.03")
    account.broker_positions = []

    with patch(
        "quantara_engine.broker.state_builder.build_competition_broker_account",
        return_value=account,
    ), patch(
        "quantara_engine.broker.execution_service.BrokerExecutionService.get_account_row",
        return_value={
            "gross_realized_pnl": Decimal("1600"),
            "fees_paid": Decimal("100"),
        },
    ), patch(
        "quantara_engine.broker.physical_risk.compute_physical_broker_risk",
        return_value={
            "physical_remaining_sl_risk_usd": None,
            "projected_broker_equity_at_stops": None,
            "physical_risk_complete": True,
            "physical_risk_missing_count": 0,
        },
    ):
        payload = broker_account_summary(store)

    assert payload["equity"] == 321500.0
    assert payload["cash"] == 320000.0
    assert payload["buying_power"] == 300000.0
    assert payload["net_realized_pnl"] == 1500.0
