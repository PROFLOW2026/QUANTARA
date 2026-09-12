"""Database session factory and dependency helpers."""

from collections.abc import Generator
from contextlib import contextmanager

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from quantara_engine.core.config import settings
from quantara_engine.db.guardrails import validate_production_database_url

_UNCONFIGURED = "postgresql://localhost:5432/quantara_unconfigured"


def _database_url() -> str:
    url = settings.database_url.strip()
    return url if url else _UNCONFIGURED


DATABASE_URL = _database_url()

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


def get_session() -> Generator[Session, None, None]:
    """FastAPI-style dependency that yields a DB session."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@contextmanager
def session_scope() -> Generator[Session, None, None]:
    """Context manager with commit/rollback."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def init_db() -> None:
    """Verify DB guardrails and connectivity against configured DATABASE_URL."""
    if not settings.database_configured:
        return
    validate_production_database_url(settings.database_url.strip())
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))