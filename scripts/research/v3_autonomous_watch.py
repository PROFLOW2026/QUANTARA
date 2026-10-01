#!/usr/bin/env python3
"""Wait for discovery complete (unique checkpoint keys), finalize, post-pipeline."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps" / "engine"))

from quantara_engine.research.v3.orchestration import (
    finalize_with_retry,
    unique_completed_count,
    wait_for_checkpoint_complete,
    write_authoritative_progress,
)

RESEARCH = ROOT / "scripts" / "research"
CHECKPOINT = RESEARCH / "v3_discovery_checkpoint.jsonl"
REGISTRY = RESEARCH / "v3_discovery_jobs.json"
PROGRESS = RESEARCH / "v3_discovery_progress.json"
LOG = RESEARCH / "v3_autonomous_watch.log"
OUTCOME = RESEARCH / "v3_final_outcome.json"


def log(msg: str) -> None:
    line = f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {msg}\n"
    LOG.open("a", encoding="utf-8").write(line)
    print(line, end="")


def run_py(script: str, *args: str) -> int:
    cmd = [sys.executable, "-u", str(ROOT / "scripts" / "research" / script), *args]
    proc = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True)
    if proc.stdout:
        log(proc.stdout.strip()[:2000])
    if proc.stderr:
        log("stderr: " + proc.stderr.strip()[:2000])
    return proc.returncode


def main() -> None:
    total = 236
    if PROGRESS.is_file():
        try:
            total = int(json.loads(PROGRESS.read_text(encoding="utf-8")).get("total") or 236)
        except Exception:
            pass

    log(f"watch start total={total} source=checkpoint_unique")
    wait_for_checkpoint_complete(checkpoint_path=CHECKPOINT, total=total, poll_seconds=45, log=log)
    write_authoritative_progress(
        progress_path=PROGRESS,
        checkpoint_path=CHECKPOINT,
        total=total,
        registry_path=REGISTRY,
    )
    run_py("v3_sync_registry_from_checkpoint.py")
    if finalize_with_retry(
        root=ROOT,
        finalize_script="v3_finalize_discovery.py",
        checkpoint_path=CHECKPOINT,
        expected_total=total,
        log=log,
    ) != 0:
        log("finalize failed after retries")
        sys.exit(1)
    run_py("v3_post_discovery_pipeline.py")
    log(f"done outcome={OUTCOME}")


if __name__ == "__main__":
    main()
