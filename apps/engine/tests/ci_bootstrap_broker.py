#!/usr/bin/env python3
"""GitHub Actions: migrate disposable broker test database."""
from __future__ import annotations

import sys
import traceback
from pathlib import Path

# `python tests/ci_bootstrap_broker.py` puts `tests/` on sys.path[0], not the engine root.
_ENGINE_ROOT = Path(__file__).resolve().parents[1]
if str(_ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(_ENGINE_ROOT))

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
