"""Attribution flip / physical exit quantity tests (in-memory via Replay V3 engine)."""

from decimal import Decimal

from quantara_engine.broker.replay_v3 import ReplayV3Event, replay_v3


def test_a_long_100_b_sell_100_physical_zero_both_shadow_exit():
    pid_a = "portfolio-a"
    pid_b = "portfolio-b"
    spid_a = "pos-a"
    spid_b = "pos-b"
    events = [
        ReplayV3Event("entry", "NVDA", "stock", "long", Decimal("100"), Decimal("100"), pid_a, spid_a),
        ReplayV3Event("entry", "NVDA", "stock", "short", Decimal("100"), Decimal("100"), pid_b, spid_b),
        ReplayV3Event("exit", "NVDA", "stock", "short", Decimal("100"), Decimal("105"), pid_a, spid_a),
        ReplayV3Event("exit", "NVDA", "stock", "long", Decimal("100"), Decimal("105"), pid_b, spid_b),
    ]
    result = replay_v3(events, starting_cash=Decimal("320000"))
    assert result["physical_quantity_difference"] == 0.0
    assert result["attribution_reconciliation"] == 0.0
    assert result["open_positions"] == 0
    assert result["orphan_exits"] == 2
    assert result["accepted"] == 2


def test_a_long_100_b_sell_60_a_exits_40_only():
    pid_a = "portfolio-a"
    pid_b = "portfolio-b"
    spid_a = "pos-a"
    events = [
        ReplayV3Event("entry", "NVDA", "stock", "long", Decimal("100"), Decimal("100"), pid_a, spid_a),
        ReplayV3Event("entry", "NVDA", "stock", "short", Decimal("60"), Decimal("105"), pid_b),
        ReplayV3Event("exit", "NVDA", "stock", "short", Decimal("100"), Decimal("110"), pid_a, spid_a),
    ]
    result = replay_v3(events, starting_cash=Decimal("320000"))
    assert result["physical_quantity_difference"] == 0.0
    assert result["attribution_reconciliation"] == 0.0
    assert result["open_positions"] == 0
    assert result["accepted"] == 3


def test_flip_b_short_50_from_a_long_100():
    pid_a = "portfolio-a"
    pid_b = "portfolio-b"
    spid_a = "pos-a"
    spid_b = "pos-b"
    events = [
        ReplayV3Event("entry", "NVDA", "stock", "long", Decimal("100"), Decimal("100"), pid_a, spid_a),
        ReplayV3Event("entry", "NVDA", "stock", "short", Decimal("150"), Decimal("95"), pid_b, spid_b),
        ReplayV3Event("exit", "NVDA", "stock", "long", Decimal("50"), Decimal("92"), pid_b, spid_b),
    ]
    result = replay_v3(events, starting_cash=Decimal("320000"))
    assert result["physical_quantity_difference"] == 0.0
    assert result["attribution_reconciliation"] == 0.0
    assert result["financial_reconciliation"] == 0.0
    assert result["open_positions"] == 0
