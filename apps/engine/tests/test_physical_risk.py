"""Physical broker risk completeness tests."""

from __future__ import annotations

from decimal import Decimal

import pytest

from quantara_engine.broker.physical_risk import compute_physical_broker_risk


def test_empty_lots_risk_complete_zero(monkeypatch):
    class FakeSnap:
        equity = Decimal("320000")

    class FakeSvc:
        def load_account_snapshot(self):
            return FakeSnap()

    monkeypatch.setattr(
        "quantara_engine.broker.execution_service.BrokerExecutionService",
        lambda store: FakeSvc(),
    )

    class FakeSession:
        def execute(self, *_args, **_kwargs):
            class R:
                def mappings(self):
                    return self

                def all(self):
                    return []

            return R()

    class FakeStore:
        session = FakeSession()

    out = compute_physical_broker_risk(FakeStore())
    assert out["physical_risk_complete"] is True
    assert out["physical_remaining_sl_risk_usd"] == Decimal("0")
    assert out["projected_broker_equity_at_stops"] == Decimal("320000")
