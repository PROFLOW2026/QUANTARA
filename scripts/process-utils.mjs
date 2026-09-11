/**
 * Process / lock inspection helpers for QUANTARA launchers (Windows-first).
 */
import fs from "fs";
import { spawnSync } from "child_process";

export const WORKER_CMD_MARKERS = ["quantara_workers.main", "-m quantara_workers.main"];
export const ENGINE_CMD_MARKERS = [
  "apps\\engine\\main.py",
  "apps/engine/main.py",
  "uvicorn main:app",
  "-m uvicorn main:app",
  "quantara_engine",
];
export const ENGINE_RELOAD_MARKERS = ["--reload", "watchfiles", "reload=True"];

export function isProcessAlive(pid) {
  if (!Number.isFinite(pid) || pid <= 0) return false;
  if (process.platform === "win32") {
    const result = spawnSync("tasklist", ["/FI", `PID eq ${pid}`, "/FO", "CSV", "/NH"], {
      encoding: "utf8",
    });
    const line = String(result.stdout || "").trim();
    if (!line || /No tasks are running/i.test(line)) return false;
    const match = line.match(/^"[^"]+","(\d+)"/);
    return match ? parseInt(match[1], 10) === pid : false;
  }
  try {
    process.kill(pid, 0);
    return true;
  } catch {
    return false;
  }
}

export function getProcessCommandLine(pid) {
  if (!Number.isFinite(pid) || pid <= 0) return "";
  if (process.platform === "win32") {
    const result = spawnSync(
      "powershell",
      [
        "-NoProfile",
        "-Command",
        `(Get-CimInstance Win32_Process -Filter "ProcessId=${pid}" -ErrorAction SilentlyContinue | Select-Object -ExpandProperty CommandLine)`,
      ],
      { encoding: "utf8", shell: true }
    );
    return String(result.stdout || "").trim();
  }
  const result = spawnSync("ps", ["-p", String(pid), "-o", "command="], { encoding: "utf8" });
  return String(result.stdout || "").trim();
}

export function commandLineMatches(commandLine, markers) {
  const hay = String(commandLine || "").toLowerCase();
  return markers.some((marker) => hay.includes(String(marker).toLowerCase()));
}

export function isQuantaraWorkerCommandLine(commandLine) {
  return commandLineMatches(commandLine, WORKER_CMD_MARKERS);
}

export function isQuantaraEngineCommandLine(commandLine) {
  if (commandLineMatches(commandLine, ENGINE_CMD_MARKERS)) return true;
  const hay = String(commandLine || "").toLowerCase();
  // Legacy/cwd launch from apps/engine: `python main.py`
  return /\bmain\.py\b/.test(hay) && !hay.includes("quantara_workers") && !hay.includes("-m uvicorn");
}

export function isQuantaraEngineReloadSupervisor(commandLine) {
  const hay = String(commandLine || "").toLowerCase();
  if (!isQuantaraEngineCommandLine(commandLine)) return false;
  if (ENGINE_RELOAD_MARKERS.some((marker) => hay.includes(String(marker).toLowerCase()))) {
    return true;
  }
  // Legacy launcher used `python main.py` with embedded uvicorn reload (no CLI flag).
  return /\bmain\.py\b/.test(hay) && !hay.includes("-m uvicorn");
}

export function getProcessParentPid(pid) {
  if (!Number.isFinite(pid) || pid <= 0) return null;
  if (process.platform === "win32") {
    const result = spawnSync(
      "powershell",
      [
        "-NoProfile",
        "-Command",
        `(Get-CimInstance Win32_Process -Filter "ProcessId=${pid}" -ErrorAction SilentlyContinue | Select-Object -ExpandProperty ParentProcessId)`,
      ],
      { encoding: "utf8", shell: true }
    );
    const parent = parseInt(String(result.stdout || "").trim(), 10);
    return Number.isFinite(parent) && parent > 0 ? parent : null;
  }
  const result = spawnSync("ps", ["-p", String(pid), "-o", "ppid="], { encoding: "utf8" });
  const parent = parseInt(String(result.stdout || "").trim(), 10);
  return Number.isFinite(parent) && parent > 0 ? parent : null;
}

export function findQuantaraEnginePidsByCommandLine() {
  if (process.platform === "win32") {
    const result = spawnSync(
      "powershell",
      [
        "-NoProfile",
        "-Command",
        "Get-CimInstance Win32_Process -Filter \"name='python.exe'\" | Where-Object { $_.CommandLine -like '*main:app*' -or $_.CommandLine -like '*apps\\\\engine\\\\main.py*' -or $_.CommandLine -like '*apps/engine/main.py*' } | ForEach-Object { $_.ProcessId }",
      ],
      { encoding: "utf8", shell: true }
    );
    return String(result.stdout || "")
      .split(/\r?\n/)
      .map((line) => parseInt(line.trim(), 10))
      .filter((pid) => Number.isFinite(pid) && pid > 0);
  }
  const result = spawnSync("pgrep", ["-f", "uvicorn main:app|apps/engine/main.py"], {
    encoding: "utf8",
  });
  return String(result.stdout || "")
    .split(/\r?\n/)
    .map((line) => parseInt(line.trim(), 10))
    .filter((pid) => Number.isFinite(pid) && pid > 0);
}

/**
 * Collect QUANTARA Engine PIDs: port listeners, command-line matches, reload supervisors.
 */
export function findQuantaraEnginePids(port = "8000") {
  const candidates = new Set();
  for (const pid of getEngineListenerPids(port)) {
    candidates.add(pid);
    const parent = getProcessParentPid(pid);
    if (parent && isQuantaraEngineCommandLine(getProcessCommandLine(parent))) {
      candidates.add(parent);
    }
  }
  for (const pid of findQuantaraEnginePidsByCommandLine()) {
    candidates.add(pid);
  }
  const roots = new Set();
  for (const pid of candidates) {
    if (!isProcessAlive(pid)) continue;
    const cmd = getProcessCommandLine(pid);
    if (!isQuantaraEngineCommandLine(cmd)) continue;
    if (isQuantaraEngineReloadSupervisor(cmd)) {
      roots.add(pid);
      continue;
    }
    const parent = getProcessParentPid(pid);
    const parentCmd = parent ? getProcessCommandLine(parent) : "";
    if (parent && isQuantaraEngineReloadSupervisor(parentCmd)) {
      roots.add(parent);
    } else {
      roots.add(pid);
    }
  }
  return [...roots].sort((a, b) => a - b);
}

export function killQuantaraEngineProcesses(port = "8000") {
  const pids = findQuantaraEnginePids(port);
  const terminated = [];
  for (const pid of pids) {
    killProcessTree(pid);
    terminated.push(pid);
  }
  return terminated;
}

export function findQuantaraWorkerPids() {
  if (process.platform === "win32") {
    const result = spawnSync(
      "powershell",
      [
        "-NoProfile",
        "-Command",
        "Get-CimInstance Win32_Process -Filter \"name='python.exe'\" | Where-Object { $_.CommandLine -like '*quantara_workers.main*' } | ForEach-Object { $_.ProcessId }",
      ],
      { encoding: "utf8", shell: true }
    );
    return String(result.stdout || "")
      .split(/\r?\n/)
      .map((line) => parseInt(line.trim(), 10))
      .filter((pid) => Number.isFinite(pid) && pid > 0);
  }
  const result = spawnSync("pgrep", ["-f", "quantara_workers.main"], { encoding: "utf8" });
  return String(result.stdout || "")
    .split(/\r?\n/)
    .map((line) => parseInt(line.trim(), 10))
    .filter((pid) => Number.isFinite(pid) && pid > 0);
}

export function readWorkerLockPid(lockPath) {
  if (!fs.existsSync(lockPath)) return { pid: null, readable: false, busy: false };
  try {
    const raw = fs.readFileSync(lockPath, "utf8").trim();
    const pid = parseInt(raw.split(/\r?\n/)[0], 10);
    return {
      pid: Number.isFinite(pid) && pid > 0 ? pid : null,
      readable: true,
      busy: false,
    };
  } catch (err) {
    if (err && typeof err === "object" && "code" in err && err.code === "EBUSY") {
      return { pid: null, readable: false, busy: true };
    }
    return { pid: null, readable: false, busy: false, error: err };
  }
}

export function removeWorkerLockFile(lockPath) {
  if (!fs.existsSync(lockPath)) return true;
  for (let attempt = 0; attempt < 5; attempt += 1) {
    try {
      fs.unlinkSync(lockPath);
      return !fs.existsSync(lockPath);
    } catch (err) {
      if (err && typeof err === "object" && "code" in err && err.code === "EBUSY") {
        spawnSync("powershell", ["-NoProfile", "-Command", "Start-Sleep -Milliseconds 400"], {
          shell: true,
        });
        continue;
      }
      return false;
    }
  }
  return !fs.existsSync(lockPath);
}

/**
 * Pure classifier — used by tests and inspectWorkerState().
 */
export function classifyWorkerLockState({
  lockExists,
  lockPid,
  lockBusy,
  lockPidAlive,
  lockPidIsWorker,
  scannedWorkerPids,
}) {
  if (!lockExists && scannedWorkerPids.length === 0) {
    return { running: false, stale: false, pid: null, reason: "no_lock_no_worker" };
  }
  if (lockPid && lockPidAlive && lockPidIsWorker) {
    return { running: true, stale: false, pid: lockPid, reason: "lock_pid_worker_alive" };
  }
  if (lockPid && !lockPidAlive) {
    return { running: false, stale: true, pid: lockPid, reason: "lock_pid_dead" };
  }
  if (lockPid && lockPidAlive && !lockPidIsWorker) {
    return { running: false, stale: true, pid: lockPid, reason: "lock_pid_not_worker" };
  }
  if (lockBusy && scannedWorkerPids.length > 0) {
    return {
      running: true,
      stale: false,
      pid: scannedWorkerPids[0],
      reason: "lock_busy_worker_scan",
    };
  }
  if (lockBusy) {
    return {
      running: true,
      stale: false,
      broken: true,
      pid: scannedWorkerPids[0] ?? null,
      reason: "lock_busy_no_worker",
    };
  }
  if (!lockPid && scannedWorkerPids.length > 0) {
    return {
      running: true,
      stale: false,
      pid: scannedWorkerPids[0],
      reason: "worker_scan_without_lock_pid",
    };
  }
  if (lockExists) {
    return { running: false, stale: true, pid: lockPid, reason: "orphan_lock" };
  }
  return { running: false, stale: false, pid: null, reason: "absent" };
}

export function inspectWorkerState(lockPath) {
  const lockExists = fs.existsSync(lockPath);
  const { pid: lockPid, busy: lockBusy } = readWorkerLockPid(lockPath);
  const scannedWorkerPids = findQuantaraWorkerPids();
  const lockPidAlive = lockPid ? isProcessAlive(lockPid) : false;
  const lockPidIsWorker = lockPidAlive
    ? isQuantaraWorkerCommandLine(getProcessCommandLine(lockPid))
    : false;
  return classifyWorkerLockState({
    lockExists,
    lockPid,
    lockBusy,
    lockPidAlive,
    lockPidIsWorker,
    scannedWorkerPids,
  });
}

export function recoverStaleWorkerLock(lockPath) {
  const state = inspectWorkerState(lockPath);
  if (!state.stale) {
    return { removed: false, state };
  }
  const removed = removeWorkerLockFile(lockPath);
  return { removed, state };
}

export function waitForProcessExit(pid, maxMs = 10000) {
  const start = Date.now();
  while (Date.now() - start < maxMs) {
    if (!isProcessAlive(pid)) return true;
    spawnSync("powershell", ["-NoProfile", "-Command", "Start-Sleep -Milliseconds 250"], {
      shell: true,
    });
  }
  return !isProcessAlive(pid);
}

export function killProcessTree(pid) {
  if (!Number.isFinite(pid) || pid <= 0) return false;
  if (process.platform === "win32") {
    spawnSync("taskkill", ["/PID", String(pid), "/F", "/T"], { stdio: "ignore" });
    return true;
  }
  try {
    process.kill(pid, "SIGTERM");
    return true;
  } catch {
    return false;
  }
}

export function getEngineListenerPids(port = "8000") {
  if (process.platform !== "win32") {
    const result = spawnSync("lsof", ["-i", `TCP:${port}`, "-sTCP:LISTEN", "-t"], {
      encoding: "utf8",
    });
    return String(result.stdout || "")
      .split(/\r?\n/)
      .map((line) => parseInt(line.trim(), 10))
      .filter((pid) => Number.isFinite(pid) && pid > 0);
  }
  const result = spawnSync("netstat", ["-ano"], { encoding: "utf8" });
  const pids = new Set();
  for (const line of String(result.stdout || "").split(/\r?\n/)) {
    if (!line.includes(`:${port}`) || !/LISTENING/i.test(line)) continue;
    const parts = line.trim().split(/\s+/);
    const pid = parseInt(parts[parts.length - 1], 10);
    if (Number.isFinite(pid) && pid > 0) pids.add(pid);
  }
  return [...pids];
}
