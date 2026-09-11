"""Replay V3 reconciliation tests."""

from decimal import Decimal

from quantara_engine.broker.replay_v3 import ReplayV3Event, replay_v3


def _events():
    return [
        ReplayV3Event(
            kind="entry",
            symbol="NVDA",
            asset_class="stock",
            direction="long",
            quantity=Decimal("10"),
            price=Decimal("170"),
            portfolio_id="p-a",
            strategy_position_id="sp-a",
            fees=Decimal("1.50"),
        ),
        ReplayV3Event(
            kind="entry",
            symbol="NVDA",
            asset_class="stock",
            direction="long",
            quantity=Decimal("5"),
            price=Decimal("175"),
            portfolio_id="p-b",
            strategy_position_id="sp-b",
            fees=Decimal("0.75"),
        ),
        ReplayV3Event(
            kind="mark",
            symbol="NVDA",
            asset_class="stock",
            direction="long",
            quantity=Decimal("0"),
            price=Decimal("180"),
        ),
        ReplayV3Event(
            kind="exit",
            symbol="NVDA",
            asset_class="stock",
            direction="short",
            quantity=Decimal("10"),
            price=Decimal("180"),
            portfolio_id="p-a",
            strategy_position_id="sp-a",
            fees=Decimal("1.20"),
        ),
        ReplayV3Event(
            kind="exit",
            symbol="NVDA",
            asset_class="stock",
            direction="short",
            quantity=Decimal("5"),
            price=Decimal("178"),
            portfolio_id="p-b",
            strategy_position_id="sp-b",
            fees=Decimal("1.05"),
        ),
    ]


def test_replay_v3_full_reconciliation_zero():
    result = replay_v3(_events(), starting_cash=Decimal("320000"))
    assert result["financial_reconciliation"] == 0.0
    assert result["margin_reconciliation"] == 0.0
    assert result["attribution_reconciliation"] == 0.0
    assert result["physical_quantity_difference"] == 0.0
    assert result["accepted"] == 4
    assert result["open_positions"] == 0


def test_replay_v3_gbpjpy_jpy_usd_attribution():
    events = [
        ReplayV3Event(
            kind="entry",
            symbol="GBPJPY",
            asset_class="forex",
            direction="long",
            quantity=Decimal("10000"),
            price=Decimal("200"),
            portfolio_id="fx-1",
        ),
        ReplayV3Event(
            kind="exit",
            symbol="GBPJPY",
            asset_class="forex",
            direction="short",
            quantity=Decimal("4000"),
            price=Decimal("210"),
            portfolio_id="fx-1",
        ),
    ]
    result = replay_v3(events, starting_cash=Decimal("320000"))
    assert result["attribution_reconciliation"] == 0.0
    assert result["realized_pnl"] == 266.67
    assert result["financial_reconciliation"] == 0.0


def test_replay_v3_closed_market_stock_rejected():
    events = [
        ReplayV3Event(
            kind="entry",
            symbol="TSLA",
            asset_class="stock",
            direction="long",
            quantity=Decimal("5"),
            price=Decimal("350"),
            market_open=False,
        ),
    ]
    result = replay_v3(events)
    assert result["rejected"] == 1
    assert result["accepted"] == 0
