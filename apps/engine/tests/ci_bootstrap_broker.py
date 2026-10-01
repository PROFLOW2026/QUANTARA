#!/usr/bin/env python3
"""GitHub Actions: migrate disposable broker test database."""
from __future__ import annotations

import importlib.util
import traceback
from pathlib import Path

_MIGRATE = Path(__file__).resolve().with_name("broker_ci_migrate.py")


def _bootstrap_fn():
    spec = importlib.util.spec_from_file_location("broker_ci_migrate", _MIGRATE)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {_MIGRATE}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    fn = getattr(mod, "bootstrap_broker_test_schema_for_ci", None)
    if not callable(fn):
        raise RuntimeError("bootstrap_broker_test_schema_for_ci missing")
    return fn


def main() -> None:
    url = _bootstrap_fn()()
    print("bootstrap ok", url, flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        raise
