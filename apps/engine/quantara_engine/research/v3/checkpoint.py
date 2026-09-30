"""Incremental checkpoint + single-instance lock for long V3 discovery runs."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def result_key(candidate_id: str, asset: str, timeframe: str) -> str:
    return f"{candidate_id}|{asset}|{timeframe}"


def load_completed(checkpoint_path: Path) -> dict[str, dict[str, Any]]:
    if not checkpoint_path.is_file():
        return {}
    done: dict[str, dict[str, Any]] = {}
    for line in checkpoint_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        row = json.loads(line)
        key = row.get("key") or result_key(
            row["candidate_id"], row["asset"], row["timeframe"]
        )
        done[key] = row
    return done


def append_result(checkpoint_path: Path, row: dict[str, Any]) -> None:
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    key = row.get("key") or result_key(
        str(row["candidate_id"]), str(row["asset"]), str(row["timeframe"])
    )
    row = {**row, "key": key}
    with checkpoint_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, default=str) + "\n")


def write_state(state_path: Path, payload: dict[str, Any]) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")


def acquire_run_lock(lock_path: Path) -> bool:
    """Return True if this process owns the lock."""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    pid = os.getpid()
    if lock_path.is_file():
        try:
            other = int(lock_path.read_text(encoding="utf-8").strip())
        except ValueError:
            other = 0
        if other and other != pid:
            try:
                os.kill(other, 0)
                return False
            except OSError:
                pass
    lock_path.write_text(str(pid), encoding="utf-8")
    return True


def release_run_lock(lock_path: Path) -> None:
    if lock_path.is_file():
        try:
            if int(lock_path.read_text(encoding="utf-8").strip()) == os.getpid():
                lock_path.unlink(missing_ok=True)
        except (ValueError, OSError):
            pass
