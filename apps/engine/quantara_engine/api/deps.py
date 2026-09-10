"""FastAPI dependencies."""

from collections.abc import Generator

from fastapi import Header, HTTPException, Request, status

from quantara_engine.core.config import settings
from quantara_engine.db.session import session_scope
from quantara_engine.persistence.store import TradingStore


def verify_api_key(
    request: Request,
    x_api_key: str | None = Header(default=None),
) -> str:
    # Preflight must succeed before the browser sends X-API-Key.
    if request.method == "OPTIONS":
        return x_api_key or ""
    if not x_api_key or x_api_key != settings.quantara_api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key",
        )
    return x_api_key


def get_store() -> Generator[TradingStore, None, None]:
    """Yield a TradingStore; commit on success, rollback on error."""
    with session_scope() as session:
        yield TradingStore(session)
