#!/usr/bin/env node
/**
 * Stop QUANTARA infrastructure started by START_QUANTARA.bat.
 * Does not modify trading state or database data.
 */
import fs from "fs";
import path from "path";
import { spawnSync } from "child_process";
import { getRepoRoot } from "./load-env.cjs";
import {
  isEnginePortListening,
  isWorkerRunning,
  isTunnelRunning,
} from "./dev-common.mjs";

const ROOT = getRepoRoot();
const ENGINE = path.join(ROOT, "apps", "engine");
const LOCK_PATH = path.join(ROOT, ".quantara-workers.lock");
const python = process.platform === "win32" ? "python" : "python3";

function readWorkerPid() {
  if (fs.existsSync(LOCK_PATH)) {
    try {
      const pid = parseInt(fs.readFileSync(LOCK_PATH, "utf8").trim().split(/\r?\n/)[0], 10);
      if (Number.isFinite(pid) && pid > 0) return pid;
    } catch {
      // fall through to Python helper when the live worker holds the lock
    }
  }
  const result = spawnSync(
    python,
    [
      "-c",
      "from quantara_workers.singleton import WorkerSingletonLock; lock = WorkerSingletonLock(); pid = lock._read_existing_pid(); print(pid or '')",
    ],
    { cwd: ENGINE, encoding: "utf8", shell: process.platform === "win32" }
  );
  const pid = parseInt(String(result.stdout || "").trim(), 10);
  return Number.isFinite(pid) && pid > 0 ? pid : null;
}

function killPid(pid, label) {
  if (!Number.isFinite(pid) || pid <= 0) return false;
  spawnSync("taskkill", ["/PID", String(pid), "/F", "/T"], { stdio: "ignore" });
  console.log(`Stopped ${label} (PID ${pid})`);
  return true;
}

function killMatchingPowerShell(filterScript, label) {
  const result = spawnSync("powershell", ["-NoProfile", "-Command", filterScript], {
    encoding: "utf8",
    shell: true,
  });
  const pids = String(result.stdout || "")
    .split(/\r?\n/)
    .map((line) => parseInt(line.trim(), 10))
    .filter((pid) => Number.isFinite(pid) && pid > 0);
  for (const pid of pids) {
    killPid(pid, label);
  }
  return pids.length > 0;
}

function main() {
  if (process.platform !== "win32") {
    console.error("STOP_QUANTARA is intended for Windows.");
    process.exit(1);
  }

  console.log("");
  console.log("QUANTARA STOP");
  console.log("=============");
  console.log("");

  if (isWorkerRunning()) {
    const pid = readWorkerPid();
    if (pid) {
      killPid(pid, "Worker");
    } else {
      const listed = spawnSync("tasklist", ["/V", "/FO", "CSV", "/NH"], { encoding: "utf8" });
      for (const line of String(listed.stdout || "").split(/\r?\n/)) {
        if (!/QUANTARA WORKER/i.test(line)) continue;
        const match = line.match(/"python\.exe","(\d+)"/i);
        if (match) {
          killPid(parseInt(match[1], 10), "Worker");
        }
      }
    }
  }

  if (isEnginePortListening()) {
    const port = (process.env.ENGINE_PORT || "8000").trim();
    const result = spawnSync("netstat", ["-ano"], { encoding: "utf8" });
    const pids = new Set();
    for (const line of String(result.stdout || "").split(/\r?\n/)) {
      if (!line.includes(`:${port}`) || !/LISTENING/i.test(line)) continue;
      const parts = line.trim().split(/\s+/);
      const pid = parseInt(parts[parts.length - 1], 10);
      if (Number.isFinite(pid) && pid > 0) pids.add(pid);
    }
    for (const pid of pids) {
      killPid(pid, "Engine");
    }
  }

  if (isTunnelRunning()) {
    const result = spawnSync("tasklist", ["/FI", "IMAGENAME eq cloudflared.exe", "/FO", "CSV", "/NH"], {
      encoding: "utf8",
    });
    for (const line of String(result.stdout || "").split(/\r?\n/)) {
      const match = line.match(/"cloudflared\.exe","(\d+)"/i);
      if (match) {
        killPid(parseInt(match[1], 10), "Cloudflare Tunnel");
      }
    }
  }

  console.log("");
  console.log("Done. Trading state was not modified.");
  console.log("");
}

main();
