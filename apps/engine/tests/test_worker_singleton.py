"""Worker singleton lock — stale recovery and process verification."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from quantara_workers.singleton import WorkerAlreadyRunningError, WorkerSingletonLock, is_worker_running


def test_acquire_raises_with_owner_pid_not_unknown() -> None:
    lock = WorkerSingletonLock(lock_path=Path("/tmp/test-worker.lock"))
    with patch(
        "quantara_workers.worker_state.inspect_worker_state",
        return_value={
            "status": "BROKEN",
            "running": True,
            "safe_to_start": False,
            "owner_pid": None,
            "meta_pid": 8888,
            "lock_pid": None,
            "lock_probe_detail": "byte_lock_held",
            "reason": "lock_held_no_owner",
        },
    ), patch.object(WorkerSingletonLock, "_remove_stale_lock_if_needed"), patch(
        "os.open", return_value=3
    ), patch("os.ftruncate"), patch("os.write"), patch("os.fsync"), patch(
        "msvcrt.locking", side_effect=OSError("locked")
    ):
        with pytest.raises(WorkerAlreadyRunningError) as exc:
            lock.acquire()
    assert "8888" in str(exc.value) or "STOP_QUANTARA" in str(exc.value)
    assert "unknown" not in str(exc.value).lower()


def test_is_worker_running_delegates_to_worker_state() -> None:
    with patch(
        "quantara_workers.worker_state.inspect_worker_state",
        return_value={"running": True, "status": "RUNNING", "owner_pid": 4242},
    ):
        assert is_worker_running() is True
