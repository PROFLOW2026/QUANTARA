"""Set DATABASE_URL/DIRECT_URL from broker CI env before quantara_engine imports."""

from __future__ import annotations

import os


def apply() -> None:
    url = (
        os.environ.get("DATABASE_URL", "").strip()
        or os.environ.get("BROKER_TEST_MIGRATE_URL", "").strip()
        or os.environ.get("BROKER_TEST_DATABASE_URL", "").strip()
    )
    if not url:
        return
    os.environ["DATABASE_URL"] = url
    os.environ.setdefault("DIRECT_URL", url)


apply()
