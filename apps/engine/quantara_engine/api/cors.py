"""CORS helpers — resolve allowed origins from env / repo .env on each request."""

from __future__ import annotations

import os

from quantara_engine.core.config import _REPO_ROOT, settings

ALLOW_METHODS = "DELETE, GET, HEAD, OPTIONS, PATCH, POST, PUT"
ALLOW_HEADERS = "authorization, content-type, x-api-key, x-requested-with"
MAX_AGE = "600"


def _read_cors_origins_from_env_file() -> str | None:
    env_path = _REPO_ROOT / ".env"
    if not env_path.exists():
        return None
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("CORS_ORIGINS="):
            return line.split("=", 1)[1].strip()
    return None


def resolve_cors_origins() -> list[str]:
    """Fresh origin list — env var, then repo .env file, then settings default."""
    raw = os.environ.get("CORS_ORIGINS") or _read_cors_origins_from_env_file() or settings.cors_origins
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


def is_allowed_origin(origin: str | None) -> bool:
    if not origin:
        return False
    return origin in resolve_cors_origins()


def cors_response_headers(origin: str) -> dict[str, str]:
    return {
        "Access-Control-Allow-Origin": origin,
        "Access-Control-Allow-Credentials": "true",
        "Access-Control-Allow-Methods": ALLOW_METHODS,
        "Access-Control-Allow-Headers": ALLOW_HEADERS,
        "Access-Control-Max-Age": MAX_AGE,
        "Vary": "Origin",
    }
