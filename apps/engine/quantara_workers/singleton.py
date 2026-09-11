"""Ensure only one worker scheduler runs per machine/environment."""

from __future__ import annotations

import atexit
import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[3]
LOCK_PATH = _REPO_ROOT / ".quantara-workers.lock"


class WorkerAlreadyRunningError(RuntimeError):
    """Raised when another worker scheduler holds the singleton lock."""


def is_quantara_worker_process(pid: int) -> bool:
    """True when PID is a live python process running quantara_workers.main."""
    if not WorkerSingletonLock.pid_alive(pid):
        return False
    if sys.platform == "win32":
        import subprocess

        result = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                (
                    f"(Get-CimInstance Win32_Process -Filter \"ProcessId={pid}\" "
                    "-ErrorAction SilentlyContinue | Select-Object -ExpandProperty CommandLine)"
                ),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        cmd = (result.stdout or "").lower()
        return "quantara_workers.main" in cmd or "quantara_workers\\main" in cmd
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as handle:
            cmd = handle.read().decode("utf-8", errors="ignore").lower()
        return "quantara_workers.main" in cmd
    except OSError:
        return False


class WorkerSingletonLock:
    def __init__(self, lock_path: Path | None = None) -> None:
        self.lock_path = lock_path or LOCK_PATH
        self._fd: int | None = None

    def read_existing_pid(self) -> int | None:
        return self._read_existing_pid()

    def _read_existing_pid(self) -> int | None:
        from quantara_workers.worker_state import read_lock_file_pid, read_lock_metadata

        meta = read_lock_metadata()
        if meta:
            try:
                pid = int(meta.get("pid"))
                if pid > 0:
                    return pid
            except (TypeError, ValueError):
                pass
        pid, _err = read_lock_file_pid()
        return pid

    @staticmethod
    def pid_alive(pid: int) -> bool:
        if pid <= 0:
            return False
        if sys.platform == "win32":
            import subprocess

            result = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                capture_output=True,
                text=True,
                check=False,
            )
            line = (result.stdout or "").strip()
            if not line or "No tasks are running" in line:
                return False
            import re

            match = re.match(r'^"[^"]+","(\d+)"', line)
            return bool(match and int(match.group(1)) == pid)
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        return True

    def _remove_stale_lock_if_needed(self) -> None:
        from quantara_workers.worker_state import inspect_worker_state, recover_stale_worker_artifacts

        state = inspect_worker_state()
        if state["status"] == "STALE":
            logger.warning("Removing stale worker artifacts (%s)", state.get("reason"))
            recover_stale_worker_artifacts()

    def acquire(self) -> None:
        from quantara_workers.worker_state import (
            inspect_worker_state,
            write_lock_metadata,
            WORKER_IDENTITY,
        )

        pre = inspect_worker_state()
        if pre["status"] == "RUNNING" and pre.get("owner_pid"):
            owner = pre["owner_pid"]
            if owner != os.getpid():
                raise WorkerAlreadyRunningError(
                    f"Worker scheduler already running (pid={owner}). "
                    "Stop the other worker process before starting a new one."
                )

        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        self._remove_stale_lock_if_needed()

        self._fd = os.open(str(self.lock_path), os.O_CREAT | os.O_RDWR)
        try:
            if sys.platform == "win32":
                import msvcrt

                msvcrt.locking(self._fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            post = inspect_worker_state()
            owner = post.get("owner_pid") or post.get("meta_pid") or post.get("lock_pid")
            detail = post.get("detail") or post.get("lock_probe_detail") or post.get("reason")
            if owner:
                msg = f"Worker scheduler already running (pid={owner})."
            elif post.get("status") == "BROKEN":
                msg = (
                    "Worker singleton lock is held but owner could not be verified "
                    f"({detail}). Run STOP_QUANTARA.bat to release the lock."
                )
            else:
                msg = f"Worker scheduler lock unavailable ({detail or 'unknown'})."
            raise WorkerAlreadyRunningError(
                f"{msg} Stop the other worker process before starting a new one."
            ) from exc

        os.ftruncate(self._fd, 0)
        payload = f"{os.getpid()}\n".encode()
        os.write(self._fd, payload)
        os.fsync(self._fd)
        write_lock_metadata(pid=os.getpid(), identity=WORKER_IDENTITY)
        atexit.register(self.release)
        logger.info("Worker singleton lock acquired pid=%s path=%s", os.getpid(), self.lock_path)

    def release(self) -> None:
        from quantara_workers.worker_state import clear_lock_metadata

        if self._fd is None:
            return
        try:
            if sys.platform == "win32":
                import msvcrt

                msvcrt.locking(self._fd, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._fd, fcntl.LOCK_UN)
        except OSError:
            pass
        try:
            os.close(self._fd)
        except OSError:
            pass
        self._fd = None
        clear_lock_metadata()
        try:
            self.lock_path.unlink(missing_ok=True)
        except OSError:
            pass


def inspect_worker_lock() -> dict[str, object]:
    from quantara_workers.worker_state import inspect_worker_state

    state = inspect_worker_state()
    return {
        "running": state.get("running", False),
        "pid": state.get("owner_pid"),
        "stale": state.get("status") == "STALE",
        "status": state.get("status"),
        "safe_to_start": state.get("safe_to_start"),
        "reason": state.get("reason"),
    }


def is_worker_running() -> bool:
    from quantara_workers.worker_state import inspect_worker_state

    return bool(inspect_worker_state().get("running"))
