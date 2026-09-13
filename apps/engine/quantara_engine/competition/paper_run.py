"""Paper competition run / generation boundary."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select, text

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
    # Read directly from DB — process-level settings cache must not stale run boundaries.
    from quantara_engine.models.workers import Setting as OrmSetting

    raw = store.session.scalar(
        select(OrmSetting.value).where(OrmSetting.key == CURRENT_PAPER_RUN_SETTING)
    )
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
    execution_model: str = "legacy_spot_limited",
    run_id: str | None = None,
) -> str:
    run_id = run_id or str(uuid.uuid4())
    meta = metadata or {}
    if "execution_model" not in meta:
        meta["execution_model"] = execution_model
    try:
        store.session.execute(
            text(
                """
                INSERT INTO paper_runs (
                  id, status, started_at, starting_broker_cash, metadata, execution_model
                ) VALUES (
                  :id, :st, :at, :cash, CAST(:meta AS jsonb),
                  CAST(:model AS execution_model_version)
                )
                """
            ),
            {
                "id": run_id,
                "st": PAPER_RUN_STATUS_ACTIVE,
                "at": datetime.now(timezone.utc),
                "cash": starting_broker_cash,
                "meta": json.dumps(meta),
                "model": execution_model,
            },
        )
    except Exception:
        store.session.rollback()
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
                "meta": json.dumps(meta),
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
    if get_current_paper_run_id(store) == run_id:
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


def resolve_position_paper_run_id(
    store: TradingStore, *, intent_id: str | None = None
) -> str | None:
    """Inherit paper_run_id from intent lineage or fall back to current active run."""
    if intent_id and paper_run_columns_ready(store):
        row = store.session.execute(
            text(
                """
                SELECT paper_run_id::text
                FROM order_intents
                WHERE id = CAST(:id AS uuid) AND paper_run_id IS NOT NULL
                """
            ),
            {"id": intent_id},
        ).scalar()
        if row:
            return str(row)
    return get_current_paper_run_id(store)


def repair_positions_paper_run_from_lineage(store: TradingStore) -> dict:
    """Backfill positions.paper_run_id only when entry-fill → order → intent proves the run."""
    if not paper_run_columns_ready(store):
        return {"repaired": 0, "position_ids": [], "skipped_ambiguous": 0}
    rows = store.session.execute(
        text(
            """
            UPDATE positions p
            SET paper_run_id = oi.paper_run_id
            FROM fills f
            JOIN orders o ON o.id = f.order_id
            JOIN order_intents oi ON oi.id = o.intent_id
            WHERE p.id = f.position_id
              AND p.paper_run_id IS NULL
              AND oi.paper_run_id IS NOT NULL
              AND f.side = 'entry'
            RETURNING p.id::text, oi.paper_run_id::text
            """
        )
    ).all()
    store.session.flush()
    return {
        "repaired": len(rows),
        "position_ids": [str(r[0]) for r in rows],
        "paper_run_ids": sorted({str(r[1]) for r in rows}),
    }


def count_trades_missing_paper_run_with_position_lineage(store: TradingStore) -> dict:
    """Read-only diagnostic: trades NULL on paper_run_id but position proves the run.

    Production ``trades`` rows are append-only; scope reads must use
    ``trade_scope_clause`` position-lineage fallback instead of UPDATE backfill.
    """
    if not paper_run_columns_ready(store):
        return {"count": 0, "realized_pnl": Decimal("0"), "trade_ids": []}
    rows = store.session.execute(
        text(
            """
            SELECT t.id::text, t.realized_pnl
            FROM trades t
            JOIN positions p ON p.id = t.position_id
            WHERE t.paper_run_id IS NULL
              AND p.paper_run_id IS NOT NULL
              AND t.backtest_run_id IS NULL
            """
        )
    ).all()
    pnl = sum((Decimal(str(r[1] or 0)) for r in rows), Decimal("0"))
    return {
        "count": len(rows),
        "realized_pnl": pnl,
        "trade_ids": [str(r[0]) for r in rows],
    }


def resolve_trade_paper_run_id(store: TradingStore, *, position_id: str) -> str | None:
    """Inherit paper_run_id from the closed position when available."""
    if not paper_run_columns_ready(store):
        return get_current_paper_run_id(store)
    row = store.session.execute(
        text(
            """
            SELECT paper_run_id::text
            FROM positions
            WHERE id = CAST(:id AS uuid) AND paper_run_id IS NOT NULL
            """
        ),
        {"id": position_id},
    ).scalar()
    if row:
        return str(row)
    return get_current_paper_run_id(store)


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


def trade_scope_sql_for_run(run_id: str, alias: str = "trades") -> tuple[str, dict]:
    """Canonical current-run trade scope for an explicit paper run (position-lineage fallback)."""
    return (
        f"({alias}.paper_run_id = CAST(:paper_run_id AS uuid) "
        f"OR ({alias}.paper_run_id IS NULL AND EXISTS ("
        f"SELECT 1 FROM positions p "
        f"WHERE p.id = {alias}.position_id "
        f"AND p.paper_run_id = CAST(:paper_run_id AS uuid))))",
        {"paper_run_id": run_id},
    )


def count_scoped_closed_trades(store: TradingStore, run_id: str | None) -> int:
    """Count closed trades in a paper run using canonical trade scope rules."""
    if not run_id:
        return 0
    if not paper_run_columns_ready(store):
        row = store.session.execute(
            text("SELECT COUNT(*)::int FROM trades WHERE closed_at IS NOT NULL AND backtest_run_id IS NULL")
        ).scalar()
        return int(row or 0)
    scope_sql, params = trade_scope_sql_for_run(run_id, alias="t")
    row = store.session.execute(
        text(f"SELECT COUNT(*)::int FROM trades t WHERE {scope_sql} AND t.closed_at IS NOT NULL"),
        params,
    ).scalar()
    return int(row or 0)


def trade_scope_sql(store: TradingStore, alias: str = "trades") -> tuple[str, dict]:
    run_id = get_current_paper_run_id(store)
    if paper_run_columns_ready(store) and run_id:
        return trade_scope_sql_for_run(run_id, alias=alias)
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
        return text(
            "(trades.paper_run_id = CAST(:paper_run_id AS uuid) "
            "OR (trades.paper_run_id IS NULL AND EXISTS ("
            "SELECT 1 FROM positions p "
            "WHERE p.id = trades.position_id "
            "AND p.paper_run_id = CAST(:paper_run_id AS uuid))))"
        ).bindparams(paper_run_id=run_id)
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
