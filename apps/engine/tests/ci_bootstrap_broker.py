#!/usr/bin/env python3
"""GitHub Actions: migrate disposable broker test database."""
from __future__ import annotations

import traceback

from tests.broker_integration_support import bootstrap_broker_test_schema_for_ci


def main() -> None:
    url = bootstrap_broker_test_schema_for_ci()
    print("bootstrap ok", url)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        raise
