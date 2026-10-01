"""CI-safe broker test DB migrations (no quantara_engine imports)."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import psycopg2

BROKER_TEST_DB_NAME = os.environ.get("BROKER_TEST_DB_NAME", "quantara_broker_test")


def _repo_root() -> Path:
    root = Path(__file__).resolve().parents[3]
    if (root / "packages" / "db" / "migrations").is_dir():
        return root
    for parent in Path(__file__).resolve().parents:
        if (parent / "packages" / "db" / "migrations").is_dir():
            return parent
    raise RuntimeError(f"repo root not found (started from {__file__})")


def _migrations_dir() -> Path:
    return _repo_root() / "packages" / "db" / "migrations"


MIGRATIONS_DIR = _migrations_dir()


def _postgres_available(url: str) -> bool:
    import time

    attempts = 5 if os.environ.get("GITHUB_ACTIONS") == "true" else 1
    last_exc: Exception | None = None
    for attempt in range(attempts):
        try:
            conn = psycopg2.connect(url)
            conn.close()
            return True
        except Exception as exc:
            last_exc = exc
            if attempt + 1 < attempts:
                time.sleep(1)
    if last_exc is not None and os.environ.get("GITHUB_ACTIONS") == "true":
        print(f"postgres unavailable after {attempts} tries: {last_exc}", flush=True)
    return False


def _apply_all_migrations_via_node(url: str) -> None:
    root = _repo_root()
    migrate_script = root / "scripts" / "migrate.mjs"
    if not migrate_script.is_file():
        raise RuntimeError(f"migrate script missing: {migrate_script}")
    node_bin = shutil.which("node") or "node"
    env = os.environ.copy()
    env["DATABASE_URL"] = url
    env["DIRECT_URL"] = url
    proc = subprocess.run(
        [node_bin, str(migrate_script)],
        cwd=str(root),
        env=env,
        capture_output=True,
        text=True,
        check=False,
        shell=sys.platform == "win32",
    )
    if proc.returncode != 0:
        raise RuntimeError(
            "db:migrate failed\n"
            f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
        )


def _apply_all_migrations_via_psycopg(url: str) -> None:
    migrations_dir = _migrations_dir()
    if not migrations_dir.is_dir():
        raise RuntimeError(f"migrations dir missing: {migrations_dir}")
    conn = psycopg2.connect(url)
    conn.autocommit = True
    cur = conn.cursor()
    for path in sorted(migrations_dir.glob("*.sql")):
        sql = path.read_text(encoding="utf-8")
        try:
            cur.execute(sql)
        except Exception as exc:
            msg = str(exc).lower()
            if "already exists" in msg:
                continue
            conn.close()
            raise RuntimeError(f"SQL migration failed: {path.name}: {exc}") from exc
    conn.close()


def _broker_tables_exist(url: str) -> bool:
    try:
        conn = psycopg2.connect(url)
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM broker_accounts LIMIT 1")
        conn.close()
        return True
    except Exception:
        return False


def apply_all_migrations(url: str) -> None:
    if os.environ.get("GITHUB_ACTIONS") == "true" and _broker_tables_exist(url):
        return
    node_err: Exception | None = None
    try:
        _apply_all_migrations_via_node(url)
        return
    except Exception as exc:
        node_err = exc
        print(f"node migrate failed: {exc}", flush=True)
    try:
        _apply_all_migrations_via_psycopg(url)
    except Exception as sql_err:
        raise RuntimeError(
            f"node migrate failed: {node_err}; psycopg fallback failed: {sql_err}"
        ) from sql_err


def seed_disposable_competition_data(url: str) -> None:
    """Minimal reference seed so reset tests can touch 160 competition portfolios."""
    root = _repo_root()
    env = os.environ.copy()
    env["DATABASE_URL"] = url
    env["DIRECT_URL"] = url
    env["QUANTARA_CI_BROKER_SEED"] = "1"
    scripts = (
        "seed.py",
        "seed_8_assets.py",
        "seed_competition.py",
        "seed_orb_strategy.py",
        "seed_orb_competition.py",
    )
    for name in scripts:
        script = root / "scripts" / name
        if not script.is_file():
            continue
        proc = subprocess.run(
            [sys.executable, str(script)],
            cwd=str(root),
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"seed failed: {name}\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
            )


def bootstrap_broker_test_schema_for_ci() -> str:
    """Apply migrations on an existing disposable DB (GitHub Actions services)."""
    default_test_url = os.environ.get(
        "BROKER_TEST_DATABASE_URL",
        f"postgresql://quantara:quantara@localhost:5432/{BROKER_TEST_DB_NAME}",
    )
    migrate_url = os.environ.get("BROKER_TEST_MIGRATE_URL", default_test_url).strip()
    test_url = default_test_url.strip()
    if not _postgres_available(migrate_url):
        raise RuntimeError(f"broker test database unreachable: {migrate_url}")
    if not _broker_tables_exist(migrate_url):
        apply_all_migrations(migrate_url)
    if not _broker_tables_exist(migrate_url):
        raise RuntimeError("broker schema missing after migrate")
    if test_url != migrate_url and not _postgres_available(test_url):
        if os.environ.get("BROKER_TEST_ALLOW_MIGRATE_URL") == "1":
            return migrate_url
        raise RuntimeError(f"broker test role database unreachable: {test_url}")
    if os.environ.get("BROKER_TEST_FULL_SEED") == "1":
        seed_disposable_competition_data(migrate_url)
    return test_url
