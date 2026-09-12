"""Position paper_run_id inheritance, scoped visibility, and lineage backfill."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

from quantara_engine.competition.paper_run import (
    repair_positions_paper_run_from_lineage,
    resolve_position_paper_run_id,
)
from quantara_engine.domain.types import (
    Direction,
    Mode,
    Position,
    PositionStatus,
    new_id,
)
from quantara_engine.persistence.store import TradingStore


TZ3 = timezone(timedelta(hours=3))


def _position(pid: str | None = None) -> Position:
    return Position(
        id=pid or str(uuid.uuid4()),
        portfolio_id="00000000-0000-0000-0000-000000012201",
        strategy_instance_id="00000000-0000-0000-0000-000000010001",
        instrument_id="00000000-0000-0000-0000-000000000001",
        direction=Direction.LONG,
        quantity=Decimal("1"),
        entry_price=Decimal("2500"),
        current_price=Decimal("2500"),
        stop_loss=Decimal("2400"),
        take_profit=Decimal("2600"),
        unrealized_pnl=Decimal("0"),
        status=PositionStatus.OPEN,
        opened_at=datetime(2026, 9, 13, 0, 15, tzinfo=TZ3),
    )


def test_save_position_inherits_paper_run_from_intent():
    store = MagicMock()
    store.mode = Mode.PAPER
    store._bt_uuid.return_value = None
    store.session = MagicMock()
    intent_id = str(uuid.uuid4())
    run_id = str(uuid.uuid4())

    with patch(
        "quantara_engine.competition.paper_run.paper_run_columns_ready",
        return_value=True,
    ), patch(
        "quantara_engine.competition.paper_run.resolve_position_paper_run_id",
        return_value=run_id,
    ) as resolve:
        TradingStore.save_position(store, _position(), intent_id=intent_id)
        resolve.assert_called_once_with(store, intent_id=intent_id)

    row = store.session.merge.call_args[0][0]
    assert str(row.paper_run_id) == run_id
    store.session.flush.assert_called_once()
    store.session.execute.assert_not_called()


def test_save_position_flushes_before_any_stamp_update():
    """Regression: stamp before flush left paper_run_id NULL."""
    store = MagicMock()
    store.mode = Mode.PAPER
    store._bt_uuid.return_value = None
    store.session = MagicMock()

    with patch(
        "quantara_engine.competition.paper_run.paper_run_columns_ready",
        return_value=True,
    ), patch(
        "quantara_engine.competition.paper_run.resolve_position_paper_run_id",
        return_value=str(uuid.uuid4()),
    ):
        TradingStore.save_position(store, _position(), intent_id=str(uuid.uuid4()))

    assert store.session.merge.called
    assert store.session.flush.called
    for call in store.session.execute.call_args_list:
        sql = str(call[0][0])
        assert "UPDATE positions SET paper_run_id" not in sql


def test_repair_only_provable_lineage_positions():
    store = MagicMock()
    provable_pos = str(uuid.uuid4())
    run_id = str(uuid.uuid4())
    result = MagicMock()
    result.all.return_value = [(provable_pos, run_id)]
    store.session.execute.return_value = result

    with patch(
        "quantara_engine.competition.paper_run.paper_run_columns_ready",
        return_value=True,
    ):
        report = repair_positions_paper_run_from_lineage(store)

    assert report["repaired"] == 1
    assert report["position_ids"] == [provable_pos]
    sql = str(store.session.execute.call_args[0][0])
    assert "oi.paper_run_id IS NOT NULL" in sql
    assert "p.paper_run_id IS NULL" in sql
    assert "f.side = 'entry'" in sql


def test_portfolio_summary_uses_per_portfolio_stats_dict():
    from quantara_engine.competition.service import _portfolio_summary
    from quantara_engine.domain.types import Direction, Portfolio, PortfolioStatus, RiskProfile, StrategyInstance

    portfolio = Portfolio(
        id="00000000-0000-0000-0000-000000012201",
        name="ETH/USD 15m",
        mode="paper",
        initial_capital=Decimal("2000"),
        balance=Decimal("2000"),
        equity=Decimal("2000"),
        status=PortfolioStatus.ACTIVE,
    )
    entry = {
        "portfolio": portfolio,
        "risk_profile": RiskProfile(
            id="r1",
            slug="balanced",
            name="Balanced",
            risk_per_trade_pct=Decimal("1"),
            max_open_positions=1,
            max_total_exposure_pct=Decimal("100"),
            daily_loss_limit_pct=Decimal("5"),
            max_drawdown_pct=Decimal("10"),
        ),
        "instance": StrategyInstance(
            id="00000000-0000-0000-0000-000000052201",
            portfolio_id=portfolio.id,
            strategy_version_id="00000000-0000-0000-0000-000000000099",
            strategy_slug="gold-trend-pullback",
            strategy_version="1.0.0",
            instrument_id="00000000-0000-0000-0000-000000000001",
            timeframe="15m",
            risk_profile_id="r1",
            is_active=True,
        ),
        "sort_order": 1,
    }
    pos = _position()
    stats = {"open_positions": [pos], "open_positions_count": 1, "closed_trades_count": 0}
    summary = _portfolio_summary(
        entry,
        stats,
        robot_label="Robot A",
        strategy_slug="gold-trend-pullback",
        strategy_name="Trend Pullback",
    )
    assert summary["open_positions_count"] == 1
    assert summary["open_position"] is True


def test_resolve_position_paper_run_prefers_intent_lineage():
    store = MagicMock()
    run_id = str(uuid.uuid4())
    store.session.execute.return_value.scalar.return_value = run_id
    with patch(
        "quantara_engine.competition.paper_run.paper_run_columns_ready",
        return_value=True,
    ):
        resolved = resolve_position_paper_run_id(store, intent_id=str(uuid.uuid4()))
    assert resolved == run_id
