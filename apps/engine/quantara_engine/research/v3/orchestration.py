"""Authoritative V3/V3.1 research progress and finalize with bounded retry."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable

from quantara_engine.research.v3.checkpoint import load_completed


def unique_completed_count(checkpoint_path: Path) -> int:
    return len(load_completed(checkpoint_path))


def write_authoritative_progress(
    *,
    progress_path: Path,
    checkpoint_path: Path,
    total: int,
    registry_path: Path | None = None,
) -> dict[str, Any]:
    completed = unique_completed_count(checkpoint_path)
    running = 0
    failed = 0
    if registry_path and registry_path.is_file():
        jobs = json.loads(registry_path.read_text(encoding="utf-8")).get("jobs") or {}
        running = sum(1 for j in jobs.values() if j.get("status") == "RUNNING")
        failed = sum(1 for j in jobs.values() if j.get("status") == "FAILED")
    payload = {
        "total": total,
        "completed": completed,
        "completed_unique": completed,
        "running": running,
        "failed": failed,
        "remaining": max(0, total - completed),
        "source": "checkpoint_unique_keys",
        "updated_at": time.time(),
    }
    progress_path.parent.mkdir(parents=True, exist_ok=True)
    progress_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload


def wait_for_checkpoint_complete(
    *,
    checkpoint_path: Path,
    total: int,
    poll_seconds: int = 45,
    log: Callable[[str], None] | None = None,
) -> int:
    while True:
        done = unique_completed_count(checkpoint_path)
        if log:
            log(f"progress {done}/{total}")
        if done >= total:
            return done
        time.sleep(poll_seconds)


def finalize_with_retry(
    *,
    root: Path,
    finalize_script: str,
    checkpoint_path: Path,
    expected_total: int,
    max_attempts: int = 8,
    delay_seconds: float = 15.0,
    log: Callable[[str], None] | None = None,
) -> int:
    script = root / "scripts" / "research" / finalize_script
    for attempt in range(1, max_attempts + 1):
        done = unique_completed_count(checkpoint_path)
        if done < expected_total:
            if log:
                log(f"finalize wait attempt={attempt} done={done}/{expected_total}")
            time.sleep(delay_seconds)
            continue
        proc = subprocess.run(
            [sys.executable, "-u", str(script)],
            cwd=str(root),
            capture_output=True,
            text=True,
        )
        if log:
            if proc.stdout:
                log(proc.stdout.strip()[:1500])
            if proc.stderr:
                log("stderr: " + proc.stderr.strip()[:1500])
        if proc.returncode == 0:
            return 0
        if log:
            log(f"finalize failed attempt={attempt} rc={proc.returncode}")
        time.sleep(delay_seconds)
    return 1
