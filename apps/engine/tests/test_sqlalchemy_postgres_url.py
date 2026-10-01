"""SQLAlchemy PostgreSQL URL normalization (psycopg2 driver)."""

from __future__ import annotations

from sqlalchemy import create_engine

from quantara_engine.db.sqlalchemy_url import normalize_sqlalchemy_postgres_url


def test_normalize_generic_postgresql_scheme():
    url = "postgresql://user:pass@localhost:5432/db"
    assert normalize_sqlalchemy_postgres_url(url) == (
        "postgresql+psycopg2://user:pass@localhost:5432/db"
    )


def test_create_engine_uses_psycopg2_dialect():
    url = normalize_sqlalchemy_postgres_url(
        "postgresql://user:pass@localhost:5432/db"
    )
    engine = create_engine(url)
    try:
        assert engine.dialect.driver == "psycopg2"
    finally:
        engine.dispose()
