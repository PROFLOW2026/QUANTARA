"""Canonical QUANTARA Worker process / singleton inspection (single source of truth)."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from quantara_workers.singleton import (
    LOCK_PATH,
    WorkerSingletonLock,
    is_quantara_worker_process,
)

META_PATH = LOCK_PATH.with_suffix(".meta.json")
WORKER_IDENTITY = "quantara_workers.main"
WORKER_CMD_PATTERNS = (
    "quantara_workers.main",
    "quantara_workers\\main",
    "-m quantara_workers.main",
)

Status = str  # RUNNING | STALE | MISSING | BROKEN


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_pid(value: object) -> int | None:
    try:
        pid = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return pid if pid > 0 else None


def read_lock_metadata() -> dict[str, Any] | None:
    if not META_PATH.exists():
        return None
    try:
        payload = json.loads(META_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def write_lock_metadata(*, pid: int, identity: str = WORKER_IDENTITY) -> None:
    META_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "pid": pid,
        "started_at": _now_iso(),
        "identity": identity,
        "lock_path": str(LOCK_PATH),
    }
    META_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def clear_lock_metadata() -> None:
    try:
        META_PATH.unlink(missing_ok=True)
    except OSError:
        pass


def read_lock_file_pid() -> tuple[int | None, str | None]:
    if not LOCK_PATH.exists():
        return None, None
    try:
        raw = LOCK_PATH.read_text(encoding="utf-8").strip()
        if not raw:
            return None, "empty_lock_file"
        pid = _parse_pid(raw.splitlines()[0])
        if pid is None:
            return None, "invalid_lock_pid"
        return pid, None
    except OSError as exc:
        return None, f"lock_read_error:{exc.__class__.__name__}"


def is_worker_command_line(command_line: str) -> bool:
    hay = (command_line or "").lower()
    return any(pattern.lower() in hay for pattern in WORKER_CMD_PATTERNS)


def scan_quantara_worker_pids() -> list[int]:
    pids: list[int] = []
    if sys.platform == "win32":
        ps_cmd = (
            "Get-CimInstance Win32_Process | "
            "Where-Object { $_.Name -match '^(python|python3|py)\\.exe$' -and "
            "$_.CommandLine -match 'quantara_workers(\\\\|\\.)main' } | "
            "ForEach-Object { $_.ProcessId }"
        )
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_cmd],
            capture_output=True,
            text=True,
            check=False,
        )
        for line in (result.stdout or "").splitlines():
            pid = _parse_pid(line)
            if pid is not None:
                pids.append(pid)
        return sorted(set(pids))

    result = subprocess.run(
        ["pgrep", "-f", "quantara_workers.main"],
        capture_output=True,
        text=True,
        check=False,
    )
    for line in (result.stdout or "").splitlines():
        pid = _parse_pid(line)
        if pid is not None:
            pids.append(pid)
    return sorted(set(pids))


def probe_lock_held() -> tuple[bool, str | None]:
    """Return (held, detail). held=True means another process owns the byte lock."""
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(str(LOCK_PATH), os.O_CREAT | os.O_RDWR)
    except OSError as exc:
        return True, f"open_failed:{exc.__class__.__name__}"
    try:
        if sys.platform == "win32":
            import msvcrt

            try:
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                return False, None
            except OSError as exc:
                return True, f"byte_lock_held:{exc.winerror if hasattr(exc, 'winerror') else exc.errno}"
        import fcntl

        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(fd, fcntl.LOCK_UN)
            return False, None
        except OSError as exc:
            return True, f"flock_held:{exc.errno}"
    finally:
        os.close(fd)


def _pick_running_pid(
    *,
    meta_pid: int | None,
    lock_pid: int | None,
    scanned: list[int],
) -> int | None:
    for pid in scanned:
        if is_quantara_worker_process(pid):
            return pid
    for pid in (meta_pid, lock_pid):
        if pid is not None and is_quantara_worker_process(pid):
            return pid
    return None


def inspect_worker_state() -> dict[str, Any]:
    """Canonical worker state for launcher + singleton."""
    meta = read_lock_metadata()
    meta_pid = _parse_pid(meta.get("pid")) if meta else None
    lock_pid, lock_read_error = read_lock_file_pid()
    scanned = scan_quantara_worker_pids()
    lock_held, lock_probe_detail = probe_lock_held()
    owner_pid = _pick_running_pid(meta_pid=meta_pid, lock_pid=lock_pid, scanned=scanned)

    base: dict[str, Any] = {
        "status": "MISSING",
        "running": False,
        "safe_to_start": True,
        "pid": owner_pid,
        "owner_pid": owner_pid,
        "meta_pid": meta_pid,
        "lock_pid": lock_pid,
        "scanned_pids": scanned,
        "lock_path": str(LOCK_PATH),
        "meta_path": str(META_PATH),
        "lock_exists": LOCK_PATH.exists(),
        "meta_exists": META_PATH.exists(),
        "lock_held": lock_held,
        "lock_probe_detail": lock_probe_detail,
        "lock_read_error": lock_read_error,
        "reason": "absent",
    }

    if owner_pid is not None:
        base.update(
            {
                "status": "RUNNING",
                "running": True,
                "safe_to_start": False,
                "pid": owner_pid,
                "owner_pid": owner_pid,
                "reason": "worker_process_alive",
            }
        )
        return base

    has_lock_artifact = LOCK_PATH.exists() or META_PATH.exists()
    if lock_held and not owner_pid:
        base.update(
            {
                "status": "BROKEN",
                "running": True,
                "safe_to_start": False,
                "reason": "lock_held_no_owner",
                "detail": lock_probe_detail or lock_read_error or "lock_active",
            }
        )
        return base

    if has_lock_artifact:
        stale_reason = lock_read_error or "orphan_lock_artifacts"
        if meta_pid and not WorkerSingletonLock.pid_alive(meta_pid):
            stale_reason = "meta_pid_dead"
        elif lock_pid and not WorkerSingletonLock.pid_alive(lock_pid):
            stale_reason = "lock_pid_dead"
        base.update(
            {
                "status": "STALE",
                "running": False,
                "safe_to_start": True,
                "reason": stale_reason,
            }
        )
        return base

    return base


def recover_stale_worker_artifacts() -> dict[str, Any]:
    state = inspect_worker_state()
    if state["status"] not in ("STALE", "BROKEN"):
        return {"recovered": False, "state": state}

    removed: list[str] = []
    for path in (LOCK_PATH, META_PATH):
        try:
            if path.exists():
                path.unlink(missing_ok=True)
                removed.append(str(path))
        except OSError:
            pass

    after = inspect_worker_state()
    return {
        "recovered": after["status"] in ("MISSING", "STALE") and not after.get("lock_held"),
        "removed": removed,
        "before": state,
        "after": after,
    }


def stop_all_worker_processes() -> dict[str, Any]:
    before = inspect_worker_state()
    terminated: list[int] = []
    for pid in before.get("scanned_pids") or []:
        if not WorkerSingletonLock.pid_alive(pid):
            continue
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/F", "/T"],
                capture_output=True,
                check=False,
            )
        else:
            try:
                os.kill(pid, 9)
            except OSError:
                pass
        terminated.append(pid)

    for pid in terminated:
        for _ in range(40):
            if not WorkerSingletonLock.pid_alive(pid):
                break
            if sys.platform == "win32":
                import time

                time.sleep(0.25)
            else:
                import time

                time.sleep(0.25)

    recover_stale_worker_artifacts()
    after = inspect_worker_state()
    return {
        "terminated": terminated,
        "before": before,
        "after": after,
    }


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="QUANTARA worker state inspection")
    parser.add_argument(
        "action",
        nargs="?",
        default="inspect",
        choices=("inspect", "recover", "stop"),
    )
    args = parser.parse_args()
    if args.action == "inspect":
        payload = inspect_worker_state()
    elif args.action == "recover":
        payload = recover_stale_worker_artifacts()
    else:
        payload = stop_all_worker_processes()
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
