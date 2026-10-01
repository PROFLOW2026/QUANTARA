"""Normalize PostgreSQL URLs for SQLAlchemy + psycopg2 (avoid psycopg v3 default on 2.1+)."""

from __future__ import annotations


def normalize_sqlalchemy_postgres_url(url: str) -> str:
    """Use postgresql+psycopg2 when the URL has no explicit DBAPI driver."""
    raw = (url or "").strip()
    if not raw:
        return raw
    scheme, _, rest = raw.partition("://")
    if not rest:
        return raw
    base = scheme.lower()
    if "+" in base:
        return raw
    if base in ("postgresql", "postgres"):
        return f"postgresql+psycopg2://{rest}"
    return raw
