"""Short-lived HMAC tokens for browser SSE (no API key in client)."""

from __future__ import annotations

import hashlib
import hmac
import time

from quantara_engine.core.config import settings

STREAM_TOKEN_SCOPE = "live-marks"
DEFAULT_STREAM_TOKEN_TTL_SEC = 3600


def issue_stream_token(*, ttl_sec: int = DEFAULT_STREAM_TOKEN_TTL_SEC) -> str:
    exp = int(time.time()) + max(60, ttl_sec)
    payload = f"{STREAM_TOKEN_SCOPE}:{exp}"
    sig = hmac.new(
        settings.quantara_api_key.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"{exp}.{sig}"


def verify_stream_token(token: str | None) -> bool:
    if not token or "." not in token:
        return False
    exp_raw, sig = token.rsplit(".", 1)
    try:
        exp = int(exp_raw)
    except ValueError:
        return False
    if exp < int(time.time()):
        return False
    payload = f"{STREAM_TOKEN_SCOPE}:{exp}"
    expected = hmac.new(
        settings.quantara_api_key.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, sig)
