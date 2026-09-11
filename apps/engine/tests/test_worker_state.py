"""Canonical worker_state inspection — launcher/singleton agreement."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from quantara_workers.worker_state import (
    META_PATH,
    LOCK_PATH,
    inspect_worker_state,
    probe_lock_held,
    recover_stale_worker_artifacts,
    write_lock_metadata,
)


def test_missing_state_is_safe_to_start() -> None:
    with patch("quantara_workers.worker_state.LOCK_PATH", Path("/tmp/q-no-lock.lock")), patch(
        "quantara_workers.worker_state.META_PATH", Path("/tmp/q-no-lock.meta.json")
    ), patch("quantara_workers.worker_state.scan_quantara_worker_pids", return_value=[]), patch(
        "quantara_workers.worker_state.probe_lock_held", return_value=(False, None)
    ):
        state = inspect_worker_state()
    assert state["status"] == "MISSING"
    assert state["safe_to_start"] is True
    assert state["running"] is False


def test_running_when_scan_finds_worker() -> None:
    with patch("quantara_workers.worker_state.scan_quantara_worker_pids", return_value=[4242]), patch(
        "quantara_workers.worker_state.is_quantara_worker_process", return_value=True
    ), patch("quantara_workers.worker_state.probe_lock_held", return_value=(True, "byte_lock_held")):
        state = inspect_worker_state()
    assert state["status"] == "RUNNING"
    assert state["owner_pid"] == 4242
    assert state["safe_to_start"] is False


def test_broken_when_lock_held_and_no_owner() -> None:
    """Regression: launcher must NOT treat this as safe-to-start."""
    with patch("quantara_workers.worker_state.scan_quantara_worker_pids", return_value=[]), patch(
        "quantara_workers.worker_state.read_lock_metadata", return_value=None
    ), patch(
        "quantara_workers.worker_state.read_lock_file_pid",
        return_value=(None, "lock_read_error:EBUSY"),
    ), patch("quantara_workers.worker_state.probe_lock_held", return_value=(True, "byte_lock_held")):
        state = inspect_worker_state()
    assert state["status"] == "BROKEN"
    assert state["running"] is True
    assert state["safe_to_start"] is False
    assert state["reason"] == "lock_held_no_owner"


def test_stale_when_dead_pid_in_metadata(tmp_path: Path) -> None:
    meta = tmp_path / "meta.json"
    lock = tmp_path / "lock"
    meta.write_text(json.dumps({"pid": 999999, "identity": "quantara_workers.main"}), encoding="utf-8")
    lock.write_text("999999\n", encoding="utf-8")
    with patch("quantara_workers.worker_state.META_PATH", meta), patch(
        "quantara_workers.worker_state.LOCK_PATH", lock
    ), patch("quantara_workers.worker_state.scan_quantara_worker_pids", return_value=[]), patch(
        "quantara_workers.worker_state.probe_lock_held", return_value=(False, None)
    ), patch("quantara_workers.worker_state.WorkerSingletonLock.pid_alive", return_value=False):
        state = inspect_worker_state()
    assert state["status"] == "STALE"
    assert state["safe_to_start"] is True


def test_metadata_provides_owner_when_lock_unreadable(tmp_path: Path) -> None:
    meta = tmp_path / "meta.json"
    meta.write_text(json.dumps({"pid": 7777, "identity": "quantara_workers.main"}), encoding="utf-8")
    with patch("quantara_workers.worker_state.META_PATH", meta), patch(
        "quantara_workers.worker_state.LOCK_PATH", tmp_path / "lock"
    ), patch(
        "quantara_workers.worker_state.read_lock_file_pid",
        return_value=(None, "lock_read_error:EBUSY"),
    ), patch("quantara_workers.worker_state.scan_quantara_worker_pids", return_value=[]), patch(
        "quantara_workers.worker_state.is_quantara_worker_process", return_value=True
    ), patch("quantara_workers.worker_state.probe_lock_held", return_value=(True, "byte_lock_held")):
        state = inspect_worker_state()
    assert state["status"] == "RUNNING"
    assert state["owner_pid"] == 7777


def test_recover_stale_removes_artifacts(tmp_path: Path) -> None:
    meta = tmp_path / "meta.json"
    lock = tmp_path / "lock"
    meta.write_text(json.dumps({"pid": 1}), encoding="utf-8")
    lock.write_text("1\n", encoding="utf-8")
    with patch("quantara_workers.worker_state.META_PATH", meta), patch(
        "quantara_workers.worker_state.LOCK_PATH", lock
    ), patch(
        "quantara_workers.worker_state.inspect_worker_state",
        side_effect=[
            {"status": "STALE", "running": False, "safe_to_start": True},
            {"status": "MISSING", "running": False, "safe_to_start": True, "lock_held": False},
        ],
    ):
        result = recover_stale_worker_artifacts()
    assert result["recovered"] is True
    assert not meta.exists()
    assert not lock.exists()


def test_write_lock_metadata(tmp_path: Path) -> None:
    meta = tmp_path / "meta.json"
    with patch("quantara_workers.worker_state.META_PATH", meta):
        write_lock_metadata(pid=12345)
        payload = json.loads(meta.read_text(encoding="utf-8"))
    assert payload["pid"] == 12345
    assert payload["identity"] == "quantara_workers.main"
    assert "started_at" in payload
