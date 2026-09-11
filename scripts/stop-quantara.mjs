#!/usr/bin/env node
/**
 * Stop QUANTARA infrastructure started by START_QUANTARA.bat.
 * Does not modify trading state or database data.
 */
import { spawnSync } from "child_process";
import path from "path";
import { getRepoRoot, loadRepoEnv } from "./load-env.cjs";
import {
  isEnginePortListening,
  isEngineRunning,
  isTunnelRunning,
  stopWorkerProcessesCanonical,
  getWorkerState,
} from "./dev-common.mjs";
import {
  findQuantaraEnginePids,
  getEngineListenerPids,
  getProcessCommandLine,
  isProcessAlive,
  isQuantaraEngineCommandLine,
  killQuantaraEngineProcesses,
  killProcessTree,
  waitForProcessExit,
} from "./process-utils.mjs";

const ROOT = getRepoRoot();
loadRepoEnv(path.join(ROOT, ".env"));
const ENGINE_PORT = (process.env.ENGINE_PORT || "8000").trim();

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
  const before = getWorkerState();
  console.log(
    `Worker state before stop: status=${before.status} owner=${before.pid ?? "none"} scanned=[${(before.scannedPids || []).join(", ")}]`
  );
  const result = stopWorkerProcessesCanonical();
  const terminated = result.terminated || [];
  if (terminated.length) {
    console.log(`Terminated Worker PIDs: ${terminated.join(", ")}`);
  } else {
    console.log("No live QUANTARA Worker process found to terminate.");
  }
  const after = getWorkerState();
  console.log(
    `Worker state after stop: status=${after.status} lock_held=${after.lockHeld} safe_to_start=${after.safeToStart}`
  );
  if (after.running || after.lockHeld) {
    console.log("WARNING: Worker may still be active — check Task Manager for quantara_workers.main");
  } else {
    console.log("Worker processes = 0, valid lock = 0");
  }
}

async function stopEngineAuthoritative() {
  const beforePids = findQuantaraEnginePids(ENGINE_PORT);
  if (beforePids.length) {
    console.log(`Engine candidates before stop: ${beforePids.join(", ")}`);
    for (const pid of beforePids) {
      const cmd = getProcessCommandLine(pid);
      console.log(`  PID ${pid}: ${cmd || "(no command line)"}`);
    }
  } else if (isEnginePortListening()) {
    const listeners = getEngineListenerPids(ENGINE_PORT);
    console.log(
      `Port ${ENGINE_PORT} listeners without QUANTARA command match: ${listeners.join(", ")}`
    );
    for (const pid of listeners) {
      killPid(pid, "Port listener");
    }
  } else {
    console.log(`No QUANTARA Engine process detected on port ${ENGINE_PORT}.`);
  }

  const terminated = killQuantaraEngineProcesses(ENGINE_PORT);
  if (terminated.length) {
    console.log(`Engine PIDs terminated = [${terminated.join(", ")}]`);
  } else {
    console.log("Engine PIDs terminated = []");
  }

  for (const pid of terminated) {
    waitForProcessExit(pid, 15000);
  }

  const remaining = findQuantaraEnginePids(ENGINE_PORT).filter((pid) => isProcessAlive(pid));
  const portListeners = getEngineListenerPids(ENGINE_PORT);
  const healthUp = await isEngineRunning();

  console.log(`Engine processes remaining = ${remaining.length}${remaining.length ? ` [${remaining.join(", ")}]` : ""}`);
  console.log(`Port ${ENGINE_PORT} listener = ${portListeners.length}${portListeners.length ? ` [${portListeners.join(", ")}]` : ""}`);
  console.log(`Engine health responding = ${healthUp ? "YES" : "NO"}`);

  const stopped =
    remaining.length === 0 && portListeners.length === 0 && !healthUp;
  if (stopped) {
    console.log("Engine stopped.");
  } else {
    console.log("Engine stop INCOMPLETE — manual cleanup may be required.");
  }
  return stopped;
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

async function main() {
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
  console.log("");
  await stopEngineAuthoritative();
  console.log("");
  stopTunnel();

  console.log("");
  console.log("Done. Trading state was not modified.");
  console.log("");
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
