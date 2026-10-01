#!/usr/bin/env python3
"""GitHub Actions: migrate disposable broker test database."""
from __future__ import annotations

import importlib.util
import sys
import traceback
from pathlib import Path

_ENGINE_ROOT = Path(__file__).resolve().parents[1]
_SUPPORT = _ENGINE_ROOT / "tests" / "broker_integration_support.py"


def _load_bootstrap_fn():
    engine_root_str = str(_ENGINE_ROOT)
    if sys.path[0] != engine_root_str:
        while engine_root_str in sys.path:
            sys.path.remove(engine_root_str)
        sys.path.insert(0, engine_root_str)
    spec = importlib.util.spec_from_file_location(
        "broker_integration_support",
        _SUPPORT,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load broker integration support: {_SUPPORT}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    fn = getattr(mod, "bootstrap_broker_test_schema_for_ci", None)
    if not callable(fn):
        raise RuntimeError("bootstrap_broker_test_schema_for_ci missing")
    return fn


def main() -> None:
    bootstrap = _load_bootstrap_fn()
    url = bootstrap()
    print("bootstrap ok", url, flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        raise
