"""Stable client order IDs for idempotent broker submission."""

from __future__ import annotations

import hashlib


def derive_client_order_id(*, account_slug: str, idempotency_key: str) -> str:
    """Deterministic client order ID — safe for lost-response recovery."""
    raw = f"{account_slug}:{idempotency_key}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:32]
