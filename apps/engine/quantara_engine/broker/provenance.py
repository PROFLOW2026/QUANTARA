"""Broker order provenance helpers — UUID columns vs idempotency keys."""

from __future__ import annotations

import uuid


def coerce_uuid(value: str | None) -> str | None:
    """Return canonical UUID string or None when not a valid UUID."""
    if not value:
        return None
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, AttributeError, TypeError):
        return None
