#!/usr/bin/env python3
"""Wait for 236/236 discovery, finalize, post-pipeline, optional V3.1 — local only."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RESEARCH = ROOT / "scripts" / "research"
CHECKPOINT = RESEARCH / "v3_discovery_checkpoint.jsonl"
PROGRESS = RESEARCH / "v3_discovery_progress.json"
LOG = RESEARCH / "v3_autonomous_watch.log"
OUTCOME = RESEARCH / "v3_final_outcome.json"
V31_OUTCOME = RESEARCH / "v3_1_final_outcome.json"


def log(msg: str) -> None:
    line = f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {msg}\n"
    LOG.open("a", encoding="utf-8").write(line)
    print(line, end="")


def completed_count() -> int:
    if not CHECKPOINT.is_file():
        return 0
    return sum(1 for _ in CHECKPOINT.open(encoding="utf-8") if _.strip())


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

    log(f"watch start total={total}")
    while True:
        done = completed_count()
        if done >= total:
            log(f"discovery complete {done}/{total}")
            break
        log(f"progress {done}/{total}")
        time.sleep(60)

    if run_py("v3_sync_registry_from_checkpoint.py") != 0:
        log("registry sync non-zero (continuing)")
    if run_py("v3_finalize_discovery.py") != 0:
        log("finalize failed")
        sys.exit(1)
    if run_py("v3_post_discovery_pipeline.py") != 0:
        log("post pipeline failed")
        sys.exit(1)

    report_path = RESEARCH / "v3_discovery_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    robust_n = (report.get("robustness_counts") or {}).get("ROBUST", 0)
    if robust_n == 0 and (RESEARCH / "v3_1_run_discovery.py").is_file():
        log("ROBUST=0 launching V3.1")
        if run_py("v3_1_run_discovery.py") == 0:
            run_py("v3_1_post_discovery_pipeline.py")
    log(f"done outcome={OUTCOME} robust={robust_n}")


if __name__ == "__main__":
    main()
