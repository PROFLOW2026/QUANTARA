/**
 * Canonical worker inspection via Python worker_state module.
 */
import { spawnSync } from "child_process";
import path from "path";

const python = process.platform === "win32" ? "python" : "python3";

export function runWorkerStateAction(engineDir, action = "inspect") {
  const result = spawnSync(python, ["-m", "quantara_workers.worker_state", action], {
    cwd: engineDir,
    encoding: "utf8",
    shell: process.platform === "win32",
  });
  if (result.status !== 0) {
    const err = (result.stderr || result.stdout || "worker_state failed").trim();
    throw new Error(err);
  }
  try {
    return JSON.parse(String(result.stdout || "").trim());
  } catch (err) {
    throw new Error(`Invalid worker_state JSON: ${result.stdout}`);
  }
}

export function mapWorkerStateToLauncher(state) {
  const status = state.status || "MISSING";
  return {
    status,
    running: Boolean(state.running),
    safeToStart: Boolean(state.safe_to_start),
    stale: status === "STALE",
    broken: status === "BROKEN",
    pid: state.owner_pid ?? state.pid ?? null,
    ownerPid: state.owner_pid ?? null,
    metaPid: state.meta_pid ?? null,
    lockPid: state.lock_pid ?? null,
    scannedPids: state.scanned_pids || [],
    reason: state.reason || status,
    detail: state.detail || state.lock_probe_detail || state.lock_read_error || null,
    lockHeld: Boolean(state.lock_held),
    raw: state,
  };
}
