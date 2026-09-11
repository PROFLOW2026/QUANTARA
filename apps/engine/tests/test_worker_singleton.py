"""Worker singleton lock — stale recovery and process verification."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from quantara_workers.singleton import (
    WorkerSingletonLock,
    inspect_worker_lock,
    is_quantara_worker_process,
    is_worker_running,
)


def test_no_lock_means_not_running(tmp_path: Path) -> None:
    lock_path = tmp_path / ".quantara-workers.lock"
    lock = WorkerSingletonLock(lock_path=lock_path)
    assert lock.read_existing_pid() is None
    with patch("quantara_workers.singleton.LOCK_PATH", lock_path):
        state = inspect_worker_lock()
    assert state["running"] is False
    assert state["stale"] is False


def test_stale_dead_pid_lock_removed_on_acquire(tmp_path: Path) -> None:
    lock_path = tmp_path / ".quantara-workers.lock"
    lock_path.write_text("999999\n", encoding="utf-8")
    lock = WorkerSingletonLock(lock_path=lock_path)
    with patch.object(WorkerSingletonLock, "pid_alive", return_value=False):
        lock._remove_stale_lock_if_needed()
    assert not lock_path.exists()


def test_wrong_pid_treated_as_stale(tmp_path: Path) -> None:
    lock_path = tmp_path / ".quantara-workers.lock"
    lock_path.write_text("12345\n", encoding="utf-8")
    lock = WorkerSingletonLock(lock_path=lock_path)
    with patch.object(WorkerSingletonLock, "pid_alive", return_value=True), patch(
        "quantara_workers.singleton.is_quantara_worker_process", return_value=False
    ):
        lock._remove_stale_lock_if_needed()
    assert not lock_path.exists()


def test_is_worker_running_requires_quantara_process(tmp_path: Path) -> None:
    lock_path = tmp_path / ".quantara-workers.lock"
    lock_path.write_text("4242\n", encoding="utf-8")
    with patch("quantara_workers.singleton.LOCK_PATH", lock_path), patch.object(
        WorkerSingletonLock, "pid_alive", return_value=True
    ), patch("quantara_workers.singleton.is_quantara_worker_process", return_value=True):
        assert is_worker_running() is True
    with patch("quantara_workers.singleton.LOCK_PATH", lock_path), patch.object(
        WorkerSingletonLock, "pid_alive", return_value=True
    ), patch("quantara_workers.singleton.is_quantara_worker_process", return_value=False):
        state = inspect_worker_lock()
        assert state["running"] is False
        assert state["stale"] is True


def test_is_quantara_worker_process_delegates_to_pid_alive() -> None:
    with patch.object(WorkerSingletonLock, "pid_alive", return_value=False):
        assert is_quantara_worker_process(1) is False
