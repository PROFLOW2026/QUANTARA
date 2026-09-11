#!/usr/bin/env node
/**
 * Stop QUANTARA infrastructure started by START_QUANTARA.bat.
 * Does not modify trading state or database data.
 */
import { spawnSync } from "child_process";
import { getRepoRoot } from "./load-env.cjs";
import {
  isEnginePortListening,
  isTunnelRunning,
  workerLockPath,
} from "./dev-common.mjs";
import {
  findQuantaraWorkerPids,
  getEngineListenerPids,
  getProcessCommandLine,
  isQuantaraEngineCommandLine,
  killProcessTree,
  removeWorkerLockFile,
  waitForProcessExit,
} from "./process-utils.mjs";

const ROOT = getRepoRoot();

function killPid(pid, label) {
  if (!Number.isFinite(pid) || pid <= 0) return false;
  killProcessTree(pid);
  const exited = waitForProcessExit(pid, 10000);
  console.log(
    exited
      ? `Stopped ${label} (PID ${pid})`
      : `Attempted to stop ${label} (PID ${pid}) — still running`
  );
  return exited;
}

function stopWorkers() {
  const pids = new Set(findQuantaraWorkerPids());
  const lockPath = workerLockPath();
  if (pids.size === 0) {
    console.log("No live QUANTARA Worker process found.");
  }
  for (const pid of pids) {
    killPid(pid, "Worker");
  }
  for (const pid of pids) {
    waitForProcessExit(pid, 5000);
  }
  if (removeWorkerLockFile(lockPath)) {
    console.log("Worker lock file removed.");
  } else if (findQuantaraWorkerPids().length === 0) {
    console.log("Worker lock file already absent or still busy.");
  }
}

function stopEngine() {
  const port = (process.env.ENGINE_PORT || "8000").trim();
  const pids = getEngineListenerPids(port);
  if (!pids.length) {
    console.log("No Engine listener on port 8000.");
    return;
  }
  for (const pid of pids) {
    const cmd = getProcessCommandLine(pid);
    const label = isQuantaraEngineCommandLine(cmd) ? "Engine" : "Port 8000 listener";
    killPid(pid, label);
  }
}

function stopTunnel() {
  if (!isTunnelRunning()) {
    console.log("No cloudflared tunnel process found.");
    return;
  }
  if (process.platform === "win32") {
    const result = spawnSync(
      "tasklist",
      ["/FI", "IMAGENAME eq cloudflared.exe", "/FO", "CSV", "/NH"],
      { encoding: "utf8" }
    );
    for (const line of String(result.stdout || "").split(/\r?\n/)) {
      const match = line.match(/"cloudflared\.exe","(\d+)"/i);
      if (match) {
        killPid(parseInt(match[1], 10), "Cloudflare Tunnel");
      }
    }
    return;
  }
  const result = spawnSync("pgrep", ["-f", "cloudflared"], { encoding: "utf8" });
  for (const line of String(result.stdout || "").split(/\r?\n/)) {
    const pid = parseInt(line.trim(), 10);
    if (Number.isFinite(pid) && pid > 0) {
      killPid(pid, "Cloudflare Tunnel");
    }
  }
}

function main() {
  if (process.platform !== "win32") {
    console.error("STOP_QUANTARA is intended for Windows.");
    process.exit(1);
  }

  console.log("");
  console.log("QUANTARA STOP");
  console.log("=============");
  console.log(`Project root: ${ROOT}`);
  console.log("");

  stopWorkers();
  if (isEnginePortListening()) {
    stopEngine();
  } else {
    console.log("Engine port 8000 is not listening.");
  }
  stopTunnel();

  console.log("");
  console.log("Done. Trading state was not modified.");
  console.log("");
}

main();
