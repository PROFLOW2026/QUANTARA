"""Closed trades card vs modal scope must match (current paper run)."""

from __future__ import annotations

import uuid
from decimal import Decimal
from unittest.mock import MagicMock, patch

from quantara_engine.competition.paper_run import count_trades_missing_paper_run_with_position_lineage
from quantara_engine.models.trading import Trade as OrmTrade
from quantara_engine.persistence.batch_summary import batch_asset_metrics
from quantara_engine.persistence.store import TradingStore


def test_list_trades_paper_only_uses_trade_scope_clause():
    store = MagicMock()
    store.session.scalars.return_value.all.return_value = []

    with patch(
        "quantara_engine.competition.paper_run.trade_scope_clause",
        return_value=OrmTrade.backtest_run_id.is_(None),
    ) as scope:
        TradingStore.list_trades(store, portfolio_id=str(uuid.uuid4()), paper_only=True)
        scope.assert_called_once_with(store)


def test_batch_asset_metrics_and_list_trades_share_paper_run_scope():
    """Card (batch_asset_metrics) and modal (list_trades) must filter identically."""
    store = MagicMock()
    instrument_id = str(uuid.uuid4())
    portfolio_id = str(uuid.uuid4())
    run_id = str(uuid.uuid4())

    trade_row = MagicMock()
    trade_row.instrument_id = instrument_id
    trade_row.realized_pnl = Decimal("42.5")
    trade_row.closed_at = None

    with patch(
        "quantara_engine.competition.paper_run.paper_run_columns_ready",
        return_value=True,
    ), patch(
        "quantara_engine.competition.paper_run.get_current_paper_run_id",
        return_value=run_id,
    ):
        store.session.execute.return_value.all.side_effect = [
            [],  # open positions in batch_asset_metrics
            [(instrument_id, 1, Decimal("42.5"))],  # scoped closed trades
        ]
        metrics = batch_asset_metrics(store, [portfolio_id])
        assert metrics[instrument_id].closed_trades == 1
        assert metrics[instrument_id].realized_pnl == Decimal("42.5")

        store.session.scalars.return_value.all.return_value = [trade_row]
        trades = TradingStore.list_trades(store, portfolio_id=portfolio_id, paper_only=True)
        assert len(trades) == 1


def test_count_trades_missing_paper_run_with_position_lineage_is_read_only():
    store = MagicMock()
    trade_id = str(uuid.uuid4())
    result = MagicMock()
    result.all.return_value = [(trade_id, Decimal("12.5"))]
    store.session.execute.return_value = result

    with patch(
        "quantara_engine.competition.paper_run.paper_run_columns_ready",
        return_value=True,
    ):
        report = count_trades_missing_paper_run_with_position_lineage(store)

    assert report["count"] == 1
    assert report["trade_ids"] == [trade_id]
    assert report["realized_pnl"] == Decimal("12.5")
    sql = str(store.session.execute.call_args[0][0])
    assert "UPDATE" not in sql.upper()
    assert "p.paper_run_id IS NOT NULL" in sql
    assert "t.paper_run_id IS NULL" in sql
