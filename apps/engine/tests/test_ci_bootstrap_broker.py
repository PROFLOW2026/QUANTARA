"""CI broker bootstrap script — import path and migration dir sanity."""

from __future__ import annotations

import runpy
import sys
from pathlib import Path


def test_ci_bootstrap_script_resolves_tests_import():
    engine_root = Path(__file__).resolve().parents[1]
    script = engine_root / "tests" / "ci_bootstrap_broker.py"
    saved = sys.path.copy()
    try:
        # Simulate GitHub Actions: cwd=apps/engine, argv like `python tests/ci_bootstrap_broker.py`
        sys.path = [str(script.parent), *saved]
        mod = runpy.run_path(str(script), run_name="__ci_bootstrap_import_probe__")
        assert callable(mod.get("bootstrap_broker_test_schema_for_ci"))
    finally:
        sys.path = saved


def test_migrations_dir_points_at_repo_packages():
    from tests.broker_integration_support import MIGRATIONS_DIR

    assert MIGRATIONS_DIR.is_dir()
    assert (MIGRATIONS_DIR / "0006_broker_account.sql").is_file()
