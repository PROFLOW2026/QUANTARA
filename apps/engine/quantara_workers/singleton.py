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
        return "quantara_workers.main" in cmd
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
        if not self.lock_path.exists():
            return None
        try:
            raw = self.lock_path.read_text(encoding="utf-8").strip()
            return int(raw.splitlines()[0])
        except (OSError, ValueError):
            return None

    @staticmethod
    def pid_alive(pid: int) -> bool:
        if pid <= 0:
            return False
        if sys.platform == "win32":
            import subprocess

            result = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}"],
                capture_output=True,
                text=True,
                check=False,
            )
            return str(pid) in result.stdout
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        return True

    def _remove_stale_lock_if_needed(self) -> None:
        stale_pid = self._read_existing_pid()
        if stale_pid is None and self.lock_path.exists():
            try:
                raw = self.lock_path.read_text(encoding="utf-8").strip()
                if not raw:
                    self.lock_path.unlink(missing_ok=True)
            except OSError:
                pass
            return
        if not stale_pid:
            return
        stale = not self.pid_alive(stale_pid) or not is_quantara_worker_process(stale_pid)
        if not stale:
            return
        reason = "dead pid" if not self.pid_alive(stale_pid) else "non-worker pid"
        logger.warning("Removing stale worker lock for %s pid=%s", reason, stale_pid)
        try:
            self.lock_path.unlink(missing_ok=True)
        except OSError:
            pass

    def acquire(self) -> None:
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
            existing = self._read_existing_pid()
            raise WorkerAlreadyRunningError(
                f"Worker scheduler already running (pid={existing or 'unknown'}). "
                "Stop the other worker process before starting a new one."
            ) from exc

        os.ftruncate(self._fd, 0)
        os.write(self._fd, f"{os.getpid()}\n".encode())
        os.fsync(self._fd)
        atexit.register(self.release)
        logger.info("Worker singleton lock acquired pid=%s path=%s", os.getpid(), self.lock_path)

    def release(self) -> None:
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
        try:
            self.lock_path.unlink(missing_ok=True)
        except OSError:
            pass


def inspect_worker_lock() -> dict[str, object]:
    lock = WorkerSingletonLock()
    pid = lock.read_existing_pid()
    if pid and lock.pid_alive(pid) and is_quantara_worker_process(pid):
        return {"running": True, "pid": pid, "stale": False, "reason": "lock_pid_worker_alive"}
    if pid and (not lock.pid_alive(pid) or not is_quantara_worker_process(pid)):
        return {
            "running": False,
            "pid": pid,
            "stale": True,
            "reason": "lock_pid_dead" if not lock.pid_alive(pid) else "lock_pid_not_worker",
        }
    if lock.lock_path.exists():
        return {"running": False, "pid": pid, "stale": True, "reason": "orphan_lock"}
    return {"running": False, "pid": None, "stale": False, "reason": "absent"}


def is_worker_running() -> bool:
    return bool(inspect_worker_lock().get("running"))
