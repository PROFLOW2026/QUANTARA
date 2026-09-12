"""Runtime database URL guardrails — keep production and test DBs isolated."""

from __future__ import annotations

import re
from urllib.parse import urlparse

PRODUCTION_DB_NAME = "quantara_prod"
BROKER_TEST_DB_NAME = "quantara_broker_test"

_SUPABASE_HOST_MARKERS = (
    "supabase.co",
    "supabase.com",
    "pooler.supabase",
    "supavisor",
)


def db_name_from_url(url: str) -> str | None:
    raw = (url or "").strip()
    if not raw:
        return None
    parsed = urlparse(raw)
    path = (parsed.path or "").lstrip("/")
    if not path:
        return None
    return path.split("/")[0].split("?")[0] or None


def _contains_supabase_host(url: str) -> bool:
    lowered = url.lower()
    return any(marker in lowered for marker in _SUPABASE_HOST_MARKERS)


def validate_production_database_url(url: str) -> None:
    """Refuse runtime startup when production URL targets the broker test DB."""
    name = db_name_from_url(url)
    if name == BROKER_TEST_DB_NAME:
        raise RuntimeError(
            f"DATABASE_URL points to test-only database '{BROKER_TEST_DB_NAME}'. "
            f"Set DATABASE_URL to local production '{PRODUCTION_DB_NAME}'."
        )
    if _contains_supabase_host(url):
        raise RuntimeError(
            "DATABASE_URL points at Supabase (legacy archive). "
            "Runtime requires local PostgreSQL — set DATABASE_URL to quantara_prod."
        )


def validate_broker_test_database_url(url: str, *, production_url: str | None = None) -> None:
    """Refuse broker integration tests when the test DB is production."""
    name = db_name_from_url(url)
    if name == PRODUCTION_DB_NAME:
        raise RuntimeError(
            f"BROKER_TEST_DATABASE_URL must not point to production '{PRODUCTION_DB_NAME}'."
        )
    prod_name = db_name_from_url(production_url or "")
    if prod_name and name == prod_name and name != BROKER_TEST_DB_NAME:
        raise RuntimeError(
            "BROKER_TEST_DATABASE_URL must not equal Owner DATABASE_URL. "
            "Use disposable local PostgreSQL or quantara_broker_test."
        )


def is_legacy_supabase_url(url: str) -> bool:
    return _contains_supabase_host(url)


def expected_migration_version() -> str:
    return "0007_paper_competition_runs"
