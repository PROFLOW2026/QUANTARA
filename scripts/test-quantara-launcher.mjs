import test from "node:test";
import assert from "node:assert/strict";
import {
  classifyWorkerLockState,
  commandLineMatches,
  isQuantaraEngineCommandLine,
  isQuantaraWorkerCommandLine,
} from "./process-utils.mjs";

test("no lock -> worker not running", () => {
  const state = classifyWorkerLockState({
    lockExists: false,
    lockPid: null,
    lockBusy: false,
    lockPidAlive: false,
    lockPidIsWorker: false,
    scannedWorkerPids: [],
  });
  assert.equal(state.running, false);
  assert.equal(state.stale, false);
});

test("valid lock + live worker -> duplicate prevented", () => {
  const state = classifyWorkerLockState({
    lockExists: true,
    lockPid: 4242,
    lockBusy: false,
    lockPidAlive: true,
    lockPidIsWorker: true,
    scannedWorkerPids: [4242],
  });
  assert.equal(state.running, true);
  assert.equal(state.pid, 4242);
});

test("stale lock + dead PID -> recoverable", () => {
  const state = classifyWorkerLockState({
    lockExists: true,
    lockPid: 9999,
    lockBusy: false,
    lockPidAlive: false,
    lockPidIsWorker: false,
    scannedWorkerPids: [],
  });
  assert.equal(state.running, false);
  assert.equal(state.stale, true);
  assert.equal(state.reason, "lock_pid_dead");
});

test("stale lock + unrelated live PID -> recoverable", () => {
  const state = classifyWorkerLockState({
    lockExists: true,
    lockPid: 1111,
    lockBusy: false,
    lockPidAlive: true,
    lockPidIsWorker: false,
    scannedWorkerPids: [],
  });
  assert.equal(state.running, false);
  assert.equal(state.stale, true);
  assert.equal(state.reason, "lock_pid_not_worker");
});

test("worker command line detection", () => {
  assert.equal(
    isQuantaraWorkerCommandLine("python -m quantara_workers.main"),
    true
  );
  assert.equal(isQuantaraWorkerCommandLine("python main.py"), false);
});

test("engine health should not pass on unrelated process command line", () => {
  assert.equal(isQuantaraEngineCommandLine("python something_else.py"), false);
  assert.equal(
    isQuantaraEngineCommandLine("python -m uvicorn main:app --port 8000"),
    true
  );
});

test("lock busy + scanned worker -> running", () => {
  const state = classifyWorkerLockState({
    lockExists: true,
    lockPid: null,
    lockBusy: true,
    lockPidAlive: false,
    lockPidIsWorker: false,
    scannedWorkerPids: [5555],
  });
  assert.equal(state.running, true);
  assert.equal(state.pid, 5555);
});

test("lock busy + no scanned worker -> blocked not safe-to-start", () => {
  const state = classifyWorkerLockState({
    lockExists: true,
    lockPid: null,
    lockBusy: true,
    lockPidAlive: false,
    lockPidIsWorker: false,
    scannedWorkerPids: [],
  });
  assert.equal(state.running, true);
  assert.equal(state.broken, true);
  assert.equal(state.reason, "lock_busy_no_worker");
});

test("mapWorkerStateToLauncher marks BROKEN unsafe", async () => {
  const { mapWorkerStateToLauncher } = await import("./worker-state-client.mjs");
  const mapped = mapWorkerStateToLauncher({
    status: "BROKEN",
    running: true,
    safe_to_start: false,
    owner_pid: null,
    lock_probe_detail: "byte_lock_held",
    reason: "lock_held_no_owner",
  });
  assert.equal(mapped.broken, true);
  assert.equal(mapped.safeToStart, false);
  assert.equal(mapped.running, true);
});

test("commandLineMatches is case-insensitive", () => {
  assert.equal(
    commandLineMatches("PYTHON -M QUANTARA_WORKERS.MAIN", ["quantara_workers.main"]),
    true
  );
});
