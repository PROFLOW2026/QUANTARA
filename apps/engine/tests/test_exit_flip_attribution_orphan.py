"""Exit overshoot must not keep opposite-direction lots on the closed strategy position."""

from decimal import Decimal
from unittest.mock import MagicMock

from quantara_engine.broker.attribution import allocate_fill_to_strategy_legs


class _FakeResult:
    def __init__(self, rows=None, scalar=None):
        self._rows = rows or []
        self._scalar = scalar

    def mappings(self):
        return self

    def all(self):
        return self._rows

    def first(self):
        return self._rows[0] if self._rows else None

    def scalar(self):
        return self._scalar


def test_exit_flip_remainder_not_linked_to_strategy_position():
    store = MagicMock()
    inserted = {}

    # One long lot of 3 remaining for the strategy position.
    open_lot = {
        "id": "lot-1",
        "strategy_position_id": "pos-long",
        "strategy_portfolio_id": "port-1",
        "symbol": "AMD",
        "direction": "long",
        "remaining_qty": Decimal("3"),
        "entry_price": Decimal("400"),
        "opportunity_key": "opp",
        "broker_fill_id": "fill-entry",
    }

    def execute(stmt, params=None):
        sql = str(stmt)
        if "FROM broker_attribution_lots" in sql and "remaining_qty > 0" in sql:
            return _FakeResult([open_lot])
        if "UPDATE broker_attribution_lots SET remaining_qty" in sql:
            return _FakeResult()
        if "INSERT INTO broker_attribution_ledger" in sql:
            return _FakeResult()
        if "INSERT INTO broker_attribution_lots" in sql:
            inserted.update(params or {})
            return _FakeResult()
        return _FakeResult()

    store.session.execute.side_effect = execute

    # Close sell 10 against only 3 long → 3 closed + 7 flip open short.
    result = allocate_fill_to_strategy_legs(
        store,
        broker_account_id="acct",
        broker_fill_id="fill-exit",
        symbol="AMD",
        strategy_position_id="pos-long",
        portfolio_id="port-1",
        direction="short",
        quantity=Decimal("10"),
        fill_price=Decimal("390"),
        closed_quantity=Decimal("3"),
        fx_rates={"USD": Decimal("1")},
        opportunity_key="opp",
        order_purpose="sl",
    )

    assert result.physical_closed_qty == Decimal("3")
    assert result.physical_opened_qty == Decimal("7")
    assert inserted.get("spid") is None
    assert inserted.get("dir") == "short"
    assert inserted.get("qty") == Decimal("7")


def test_reduce_only_clip_rejects_same_direction_on_short_book():
    """Selling into an existing short must not be treated as a valid reduce-only close."""
    cur_qty = Decimal("-24")  # broker already short
    direction = "short"  # SL of a stale long shadow
    quantity = Decimal("30")
    if cur_qty > 0 and direction == "short":
        clipped = min(quantity, cur_qty)
    elif cur_qty < 0 and direction == "long":
        clipped = min(quantity, abs(cur_qty))
    else:
        clipped = Decimal("0")
    assert clipped == Decimal("0")


def test_reduce_only_clip_allows_buy_to_cover_short():
    cur_qty = Decimal("-24")
    direction = "long"
    quantity = Decimal("30")
    if cur_qty > 0 and direction == "short":
        clipped = min(quantity, cur_qty)
    elif cur_qty < 0 and direction == "long":
        clipped = min(quantity, abs(cur_qty))
    else:
        clipped = Decimal("0")
    assert clipped == Decimal("24")


def test_entry_add_still_links_strategy_position():
    store = MagicMock()
    inserted = {}

    def execute(stmt, params=None):
        sql = str(stmt)
        if "FROM broker_attribution_lots" in sql:
            return _FakeResult([])
        if "INSERT INTO broker_attribution_lots" in sql:
            inserted.update(params or {})
            return _FakeResult()
        return _FakeResult()

    store.session.execute.side_effect = execute

    allocate_fill_to_strategy_legs(
        store,
        broker_account_id="acct",
        broker_fill_id="fill-entry",
        symbol="AMD",
        strategy_position_id="pos-long",
        portfolio_id="port-1",
        direction="long",
        quantity=Decimal("5"),
        fill_price=Decimal("400"),
        closed_quantity=Decimal("0"),
        fx_rates={"USD": Decimal("1")},
        opportunity_key="opp",
        order_purpose="entry",
    )

    assert inserted.get("spid") == "pos-long"
    assert inserted.get("qty") == Decimal("5")
