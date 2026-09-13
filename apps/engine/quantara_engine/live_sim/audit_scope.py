"""Scope Live Sim audit counters to the current owner baseline."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import text

from quantara_engine.competition.paper_run import get_current_paper_run_id
from quantara_engine.persistence.store import TradingStore


def _parse_ts(raw) -> datetime | None:
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return raw.astimezone(timezone.utc) if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
    text_val = str(raw).strip()
    if not text_val:
        return None
    return datetime.fromisoformat(text_val.replace("Z", "+00:00")).astimezone(timezone.utc)


def resolve_live_sim_audit_since(
    store: TradingStore,
    *,
    account_metadata: dict | None,
    activated_at: datetime | None,
) -> datetime | None:
    """
    Lower bound for Live Sim allocation audit rows shown in Home UI.

    Prefers an explicit live-sim baseline marker, then aligns with the active
    Research paper run boundary (owner clean baseline), then account activation.
    """
    meta = account_metadata or {}
    explicit = _parse_ts(meta.get("baseline_reset_at"))
    if explicit is not None:
        return explicit

    run_id = get_current_paper_run_id(store)
    if run_id:
        row = store.session.execute(
            text(
                """
                SELECT COALESCE(started_at, created_at) AS boundary_at
                FROM paper_runs
                WHERE id = CAST(:rid AS uuid)
                """
            ),
            {"rid": run_id},
        ).mappings().first()
        if row and row.get("boundary_at"):
            return _parse_ts(row["boundary_at"])

    return _parse_ts(activated_at)
