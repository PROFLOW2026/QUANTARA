"""Paper competition run / generation boundary."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import text

from quantara_engine.persistence.store import TradingStore

CURRENT_PAPER_RUN_SETTING = "current_paper_run_id"
PAPER_RUN_STATUS_ACTIVE = "active"
PAPER_RUN_STATUS_LEGACY = "legacy_simulation"

_COLUMN_CACHE: dict[str, bool] = {}


def _table_exists(store: TradingStore, table: str) -> bool:
    try:
        store.session.execute(text(f"SELECT 1 FROM {table} LIMIT 1"))
        return True
    except Exception:
        store.session.rollback()
        return False


def paper_run_columns_ready(store: TradingStore) -> bool:
    cached = _COLUMN_CACHE.get("ready")
    if cached is not None:
        return cached
    if not _table_exists(store, "paper_runs"):
        _COLUMN_CACHE["ready"] = False
        return False
    try:
        store.session.execute(text("SELECT paper_run_id FROM trades LIMIT 0"))
        _COLUMN_CACHE["ready"] = True
        return True
    except Exception:
        store.session.rollback()
        _COLUMN_CACHE["ready"] = False
        return False


def get_current_paper_run_id(store: TradingStore) -> str | None:
    if not _table_exists(store, "paper_runs"):
        return None
    settings = store.get_settings_dict()
    raw = settings.get(CURRENT_PAPER_RUN_SETTING)
    if raw:
        return str(raw)
    row = store.session.execute(
        text(
            """
            SELECT id::text FROM paper_runs
            WHERE status = :st
            ORDER BY started_at DESC
            LIMIT 1
            """
        ),
        {"st": PAPER_RUN_STATUS_ACTIVE},
    ).scalar()
    return str(row) if row else None


def create_paper_run(
    store: TradingStore,
    *,
    starting_broker_cash: Decimal,
    metadata: dict | None = None,
) -> str:
    run_id = str(uuid.uuid4())
    store.session.execute(
        text(
            """
            INSERT INTO paper_runs (
              id, status, started_at, starting_broker_cash, metadata
            ) VALUES (
              :id, :st, :at, :cash, CAST(:meta AS jsonb)
            )
            """
        ),
        {
            "id": run_id,
            "st": PAPER_RUN_STATUS_ACTIVE,
            "at": datetime.now(timezone.utc),
            "cash": starting_broker_cash,
            "meta": json.dumps(metadata or {}),
        },
    )
    store.update_settings(CURRENT_PAPER_RUN_SETTING, run_id)
    _COLUMN_CACHE.clear()
    return run_id


def end_paper_run(store: TradingStore, run_id: str) -> None:
    store.session.execute(
        text(
            """
            UPDATE paper_runs SET status = :st, ended_at = NOW()
            WHERE id = :id AND status = :active
            """
        ),
        {"id": run_id, "st": PAPER_RUN_STATUS_LEGACY, "active": PAPER_RUN_STATUS_ACTIVE},
    )
    settings = store.get_settings_dict()
    if str(settings.get(CURRENT_PAPER_RUN_SETTING) or "") == run_id:
        store.update_settings(CURRENT_PAPER_RUN_SETTING, None)


def paper_run_uuid(store: TradingStore) -> uuid.UUID | None:
    if not paper_run_columns_ready(store):
        return None
    run_id = get_current_paper_run_id(store)
    if not run_id:
        return None
    try:
        return uuid.UUID(run_id)
    except ValueError:
        return None


_STAMP_TABLES = frozenset(
    {"positions", "trades", "order_intents", "portfolio_snapshots", "decisions", "signals"}
)


def stamp_paper_run_id(store: TradingStore, *, table: str, row_id: str) -> None:
    """Attach current paper run to a persisted row when 0007 columns exist."""
    run_id = get_current_paper_run_id(store)
    if not run_id or not paper_run_columns_ready(store):
        return
    if table not in _STAMP_TABLES:
        return
    store.session.execute(
        text(f"UPDATE {table} SET paper_run_id = CAST(:run_id AS uuid) WHERE id = CAST(:id AS uuid)"),
        {"run_id": run_id, "id": row_id},
    )


def _scoped_run_id(store: TradingStore) -> str | None:
    if not paper_run_columns_ready(store):
        return None
    return get_current_paper_run_id(store)


def snapshot_scope_clause(store: TradingStore):
    from quantara_engine.models.portfolio import PortfolioSnapshot as OrmPortfolioSnapshot

    run_id = _scoped_run_id(store)
    if run_id:
        return text("portfolio_snapshots.paper_run_id = CAST(:paper_run_id AS uuid)").bindparams(
            paper_run_id=run_id
        )
    return OrmPortfolioSnapshot.backtest_run_id.is_(None)


def decision_scope_clause(store: TradingStore):
    from quantara_engine.models.trading import Decision as OrmDecision

    run_id = _scoped_run_id(store)
    if run_id:
        return text("decisions.paper_run_id = CAST(:paper_run_id AS uuid)").bindparams(
            paper_run_id=run_id
        )
    return OrmDecision.backtest_run_id.is_(None)


def signal_scope_clause(store: TradingStore):
    from quantara_engine.models.trading import Signal as OrmSignal

    run_id = _scoped_run_id(store)
    if run_id:
        return text("signals.paper_run_id = CAST(:paper_run_id AS uuid)").bindparams(
            paper_run_id=run_id
        )
    return OrmSignal.backtest_run_id.is_(None)


def trade_scope_sql(store: TradingStore, alias: str = "trades") -> tuple[str, dict]:
    run_id = get_current_paper_run_id(store)
    if paper_run_columns_ready(store) and run_id:
        return f"{alias}.paper_run_id = CAST(:paper_run_id AS uuid)", {"paper_run_id": run_id}
    return f"{alias}.backtest_run_id IS NULL", {}


def position_scope_sql(store: TradingStore, alias: str = "positions") -> tuple[str, dict]:
    run_id = get_current_paper_run_id(store)
    if paper_run_columns_ready(store) and run_id:
        return f"{alias}.paper_run_id = CAST(:paper_run_id AS uuid)", {"paper_run_id": run_id}
    return f"{alias}.backtest_run_id IS NULL", {}


def intent_scope_sql(store: TradingStore, alias: str = "order_intents") -> tuple[str, dict]:
    run_id = get_current_paper_run_id(store)
    if paper_run_columns_ready(store) and run_id:
        return f"{alias}.paper_run_id = CAST(:paper_run_id AS uuid)", {"paper_run_id": run_id}
    return f"{alias}.backtest_run_id IS NULL", {}


def trade_scope_clause(store: TradingStore):
    from quantara_engine.models.trading import Trade as OrmTrade

    run_id = get_current_paper_run_id(store)
    if paper_run_columns_ready(store) and run_id:
        return text("trades.paper_run_id = CAST(:paper_run_id AS uuid)").bindparams(
            paper_run_id=run_id
        )
    return OrmTrade.backtest_run_id.is_(None)


def position_scope_clause(store: TradingStore):
    from quantara_engine.models.trading import Position as OrmPosition

    run_id = get_current_paper_run_id(store)
    if paper_run_columns_ready(store) and run_id:
        return text("positions.paper_run_id = CAST(:paper_run_id AS uuid)").bindparams(
            paper_run_id=run_id
        )
    return OrmPosition.backtest_run_id.is_(None)


def intent_scope_clause(store: TradingStore):
    from quantara_engine.models.trading import OrderIntent as OrmOrderIntent

    run_id = get_current_paper_run_id(store)
    if paper_run_columns_ready(store) and run_id:
        return text("order_intents.paper_run_id = CAST(:paper_run_id AS uuid)").bindparams(
            paper_run_id=run_id
        )
    return OrmOrderIntent.backtest_run_id.is_(None)
