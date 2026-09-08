"""Tests for worker singleton lock behavior."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from quantara_workers.singleton import WorkerSingletonLock


def test_singleton_lock_prevents_second_acquire(tmp_path: Path):
    lock_path = tmp_path / "worker.lock"
    first = WorkerSingletonLock(lock_path)
    second = WorkerSingletonLock(lock_path)
    first.acquire()
    try:
        with pytest.raises(RuntimeError):
            second.acquire()
    finally:
        first.release()


def test_stale_lock_removed_when_pid_dead(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    lock_path = tmp_path / "worker.lock"
    lock_path.write_text("999999\n", encoding="utf-8")
    monkeypatch.setattr(WorkerSingletonLock, "pid_alive", staticmethod(lambda _pid: False))

    lock = WorkerSingletonLock(lock_path)
    lock.acquire()
    try:
        assert lock._fd is not None
    finally:
        lock.release()
