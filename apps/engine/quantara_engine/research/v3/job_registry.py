"""Shared job registry for sharded V3 discovery (local file lock)."""

from __future__ import annotations

import json
import os
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from quantara_engine.research.v3.checkpoint import result_key

JobStatus = str  # PENDING | RUNNING | DONE | FAILED


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


@contextmanager
def registry_lock(lock_path: Path) -> Iterator[None]:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fh = open(lock_path, "a+b")
    try:
        if os.name == "nt":
            import msvcrt

            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        try:
            if os.name == "nt":
                import msvcrt

                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        finally:
            fh.close()


def load_registry(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"jobs": {}}
    return json.loads(path.read_text(encoding="utf-8"))


def save_registry(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    tmp.replace(path)


def claim_job(
    *,
    registry_path: Path,
    lock_path: Path,
    job_key: str,
    pid: int,
    stale_seconds: int = 7200,
) -> bool:
    """Return True if this pid owns the job for execution."""
    with registry_lock(lock_path):
        reg = load_registry(registry_path)
        jobs: dict[str, Any] = reg.setdefault("jobs", {})
        entry = jobs.get(job_key) or {"status": "PENDING"}
        status = entry.get("status", "PENDING")
        if status == "DONE":
            return False
        if status == "RUNNING":
            other = int(entry.get("pid") or 0)
            updated = entry.get("updated_at")
            age_ok = True
            if updated:
                try:
                    ts = datetime.fromisoformat(str(updated).replace("Z", "+00:00"))
                    age_ok = (datetime.now(timezone.utc) - ts).total_seconds() < stale_seconds
                except ValueError:
                    age_ok = False
            if other != pid and _pid_alive(other) and age_ok:
                return False
        jobs[job_key] = {
            "status": "RUNNING",
            "pid": pid,
            "updated_at": _utc_now(),
            "candidate_id": entry.get("candidate_id"),
            "asset": entry.get("asset"),
            "timeframe": entry.get("timeframe"),
        }
        save_registry(registry_path, reg)
        return True


def finish_job(
    *,
    registry_path: Path,
    lock_path: Path,
    job_key: str,
    pid: int,
    status: JobStatus,
) -> None:
    with registry_lock(lock_path):
        reg = load_registry(registry_path)
        jobs: dict[str, Any] = reg.setdefault("jobs", {})
        entry = jobs.get(job_key) or {}
        if int(entry.get("pid") or 0) not in (0, pid):
            return
        jobs[job_key] = {
            **entry,
            "status": status,
            "pid": pid,
            "updated_at": _utc_now(),
        }
        save_registry(registry_path, reg)


def init_registry_jobs(
    registry_path: Path,
    lock_path: Path,
    planned: list[dict[str, str]],
) -> None:
    with registry_lock(lock_path):
        reg = load_registry(registry_path)
        jobs: dict[str, Any] = reg.setdefault("jobs", {})
        for spec in planned:
            key = spec["key"]
            if key not in jobs:
                jobs[key] = {
                    "status": "PENDING",
                    "candidate_id": spec["candidate_id"],
                    "asset": spec["asset"],
                    "timeframe": spec["timeframe"],
                }
            elif jobs[key].get("status") == "PENDING":
                jobs[key].update(
                    {
                        "candidate_id": spec["candidate_id"],
                        "asset": spec["asset"],
                        "timeframe": spec["timeframe"],
                    }
                )
        reg["total"] = len(planned)
        save_registry(registry_path, reg)


def job_key_for(candidate_id: str, asset: str, timeframe: str) -> str:
    return result_key(candidate_id, asset, timeframe)
