"""Execution model versioning — separates legacy vs realistic broker simulation."""

from __future__ import annotations

from enum import Enum


class ExecutionModelVersion(str, Enum):
    LEGACY_SPOT_LIMITED = "legacy_spot_limited"
    REALISTIC_BROKER_V1 = "realistic_broker_v1"


DEFAULT_EXECUTION_MODEL = ExecutionModelVersion.LEGACY_SPOT_LIMITED
ACTIVE_EXECUTION_MODEL = ExecutionModelVersion.REALISTIC_BROKER_V1


def parse_execution_model(value: str | None) -> ExecutionModelVersion:
    if not value:
        return DEFAULT_EXECUTION_MODEL
    try:
        return ExecutionModelVersion(str(value).lower())
    except ValueError:
        return DEFAULT_EXECUTION_MODEL


def uses_realistic_broker(model: ExecutionModelVersion) -> bool:
    return model == ExecutionModelVersion.REALISTIC_BROKER_V1


def resolve_execution_model(store, account_slug: str) -> ExecutionModelVersion:
    """Load execution model from broker_accounts; fallback to legacy."""
    from sqlalchemy import text

    try:
        row = store.session.execute(
            text(
                """
                SELECT execution_model::text AS execution_model
                FROM broker_accounts WHERE slug = :slug LIMIT 1
                """
            ),
            {"slug": account_slug},
        ).mappings().first()
    except Exception:
        store.session.rollback()
        row = None
    if row and row.get("execution_model"):
        return parse_execution_model(str(row["execution_model"]))
    return DEFAULT_EXECUTION_MODEL


def resolve_execution_model_for_paper_run(store) -> ExecutionModelVersion:
    """Active paper run execution model when column exists."""
    from sqlalchemy import text

    try:
        row = store.session.execute(
            text(
                """
                SELECT execution_model::text AS execution_model
                FROM paper_runs
                WHERE status = 'active'
                ORDER BY started_at DESC
                LIMIT 1
                """
            ),
        ).mappings().first()
    except Exception:
        store.session.rollback()
        row = None
    if row and row.get("execution_model"):
        return parse_execution_model(str(row["execution_model"]))
    return DEFAULT_EXECUTION_MODEL
