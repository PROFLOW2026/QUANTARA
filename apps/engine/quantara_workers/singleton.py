"""Ensure only one worker scheduler runs per machine/environment."""

from __future__ import annotations

import atexit
import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[2]
LOCK_PATH = _REPO_ROOT / ".quantara-workers.lock"


class WorkerAlreadyRunningError(RuntimeError):
    """Raised when another worker scheduler holds the singleton lock."""


class WorkerSingletonLock:
    def __init__(self, lock_path: Path | None = None) -> None:
        self.lock_path = lock_path or LOCK_PATH
        self._fd: int | None = None

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

    def acquire(self) -> None:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        stale_pid = self._read_existing_pid()
        if stale_pid and not self.pid_alive(stale_pid):
            logger.warning("Removing stale worker lock for dead pid=%s", stale_pid)
            try:
                self.lock_path.unlink(missing_ok=True)
            except OSError:
                pass

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


def is_worker_running() -> bool:
    lock = WorkerSingletonLock()
    pid = lock._read_existing_pid()
    return bool(pid and lock.pid_alive(pid))
