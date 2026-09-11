#!/usr/bin/env node
/**
 * One-click QUANTARA infrastructure launcher (Windows-friendly).
 * Starts Engine, Worker/Scheduler, and Cloudflare Quick Tunnel.
 * Does not modify trading state, DB data, or Vercel configuration.
 */
import { spawnSync } from "child_process";
import {
  prepareDevEnv,
  engineService,
  workersService,
  isEngineRunning,
  isEnginePortListening,
  getEngineListenerPid,
  getVerifiedEnginePids,
  isWorkerRunning,
  getWorkerRunningPid,
  getWorkerState,
  recoverWorkerStateIfNeeded,
  isWorkerSafeToStart,
  isTunnelRunning,
  quickTunnelService,
  startWindowsTerminal,
  waitForEngine,
  waitForWorker,
  waitForTunnel,
  printTunnelBanner,
} from "./dev-common.mjs";
import {
  getProcessCommandLine,
  isQuantaraEngineCommandLine,
  killProcessTree,
  waitForProcessExit,
} from "./process-utils.mjs";

const { ROOT, ENGINE } = prepareDevEnv();

function log(line = "") {
  console.log(line);
}

function statusLine(label, ok, detail) {
  const mark = ok ? "PASS" : "FAIL";
  console.log(`${label.padEnd(28)} ${mark}${detail ? ` — ${detail}` : ""}`);
}

function ensureCloudflared() {
  const check = spawnSync("cloudflared", ["--version"], {
    stdio: "ignore",
  });
  if (check.status !== 0) {
    console.error("cloudflared not found.");
    console.error("Install: winget install Cloudflare.cloudflared");
    process.exit(1);
  }
}

async function clearStaleEngineListeners() {
  if (await isEngineRunning()) return false;
  if (!isEnginePortListening()) return false;
  const pid = getEngineListenerPid();
  if (!pid) return false;
  const cmd = getProcessCommandLine(pid);
  if (isQuantaraEngineCommandLine(cmd)) {
    log(`[ENGINE] Port 8000 held by stale engine PID=${pid} — terminating`);
  } else {
    log(`[ENGINE] Port 8000 held by non-engine PID=${pid} — terminating`);
  }
  killProcessTree(pid);
  await waitForProcessExit(pid, 10000);
  return true;
}

async function launchEngine() {
  if (await isEngineRunning()) {
    const pids = getVerifiedEnginePids();
    const pid = pids[0] ?? null;
    if (!pid) {
      log("[ENGINE] Health OK but no verifiable Engine PID — reporting inconsistency");
      return { ok: true, started: false, pid: null, pidWarning: true };
    }
    log(`[ENGINE] Already running PID=${pid} — health OK`);
    return { ok: true, started: false, pid };
  }
  await clearStaleEngineListeners();
  const service = engineService();
  service.label = "QUANTARA ENGINE";
  startWindowsTerminal(service);
  log("[ENGINE] Starting in new window...");
  const up = await waitForEngine();
  const pid = getVerifiedEnginePids()[0] ?? null;
  if (up && !pid) {
    log("[ENGINE] Started but PID could not be verified — check QUANTARA ENGINE window");
  }
  return { ok: up, started: true, pid };
}

async function launchWorker() {
  let state = getWorkerState();
  if (state.running && state.pid) {
    log(`[WORKER] Already running PID=${state.pid} — skipping duplicate start`);
    return { ok: true, started: false, pid: state.pid };
  }
  if (state.broken) {
    log(
      `[WORKER] BLOCKED — singleton lock held (${state.detail || state.reason}). Run STOP_QUANTARA.bat first.`
    );
    return { ok: false, started: false, pid: null, blocked: true };
  }

  const recovery = recoverWorkerStateIfNeeded();
  if (recovery.recovered) {
    log(`[WORKER] Stale worker artifacts removed (${recovery.before.reason})`);
  }
  state = recovery.after || getWorkerState();
  if (!state.safeToStart) {
    log(`[WORKER] BLOCKED — unsafe to start (state=${state.status}, ${state.reason})`);
    return { ok: false, started: false, pid: null, blocked: true };
  }

  const service = workersService();
  if (!service) {
    const pid = getWorkerRunningPid();
    return { ok: Boolean(pid && isWorkerRunning()), started: false, pid };
  }
  service.label = "QUANTARA WORKER";
  startWindowsTerminal(service);
  log("[WORKER] Starting in new window...");
  const up = await waitForWorker();
  return { ok: up, started: true, pid: getWorkerRunningPid() };
}

async function launchTunnel() {
  if (isTunnelRunning()) {
    log("[TUNNEL] Quick tunnel already running — skipping duplicate start");
    return { ok: true, started: false };
  }

  let publicUrl = null;
  const urlPattern = /https:\/\/[a-z0-9-]+\.trycloudflare\.com/i;
  const service = quickTunnelService((line) => {
    const match = line.match(urlPattern);
    if (match && !publicUrl) {
      publicUrl = match[0];
      printTunnelBanner(publicUrl);
    }
  });
  service.label = "QUANTARA TUNNEL";
  service.preLines = [
    "Quick Tunnel — copy the https://....trycloudflare.com URL below",
    "into Vercel ENGINE_URL (manual update each restart).",
  ];
  startWindowsTerminal(service);
  log("[TUNNEL] Starting quick tunnel in new window...");
  const up = await waitForTunnel();
  if (publicUrl) {
    printTunnelBanner(publicUrl);
  } else {
    log("");
    log("Quick Tunnel URL: see the QUANTARA TUNNEL window for https://....trycloudflare.com");
    log("Update Vercel ENGINE_URL manually when the URL changes.");
    log("");
  }
  return { ok: up, started: true, publicUrl };
}

async function main() {
  if (process.platform !== "win32") {
    console.error("START_QUANTARA is intended for Windows double-click use.");
    console.error("On this platform, use: npm run dev:remote");
    process.exit(1);
  }

  ensureCloudflared();

  log("");
  log("QUANTARA ONE-CLICK STARTER");
  log("=========================");
  log(`Project root: ${ROOT}`);
  log(`Engine dir:   ${ENGINE}`);
  log("");

  const engine = await launchEngine();
  const worker = await launchWorker();
  const tunnel = await launchTunnel();

  log("");
  log("Health checks");
  log("-------------");

  let engineUp = await isEngineRunning();
  if (!engineUp && engine.started) {
    engineUp = await waitForEngine(30000);
  }

  let workerUp = isWorkerRunning();
  if (!workerUp && worker.started) {
    workerUp = await waitForWorker(30000);
  }
  const workerPid = getWorkerRunningPid();

  let tunnelUp = isTunnelRunning();
  if (!tunnelUp && tunnel.started) {
    tunnelUp = await waitForTunnel(30000);
  }

  statusLine(
    "Engine",
    engineUp,
    engineUp ? "http://127.0.0.1:8000/api/v1/health" : "health endpoint not responding"
  );
  const workerState = getWorkerState();
  statusLine(
    "Worker + Scheduler",
    workerUp,
    workerUp
      ? `quantara_workers.main PID=${workerPid ?? "?"}`
      : worker.blocked
        ? `blocked (${workerState.detail || workerState.reason})`
        : "no live worker process"
  );
  statusLine(
    "Quick Cloudflare Tunnel",
    tunnelUp,
    tunnelUp ? "cloudflared process running" : "not detected"
  );

  log("");
  const allCore = engineUp && workerUp && tunnelUp;
  if (allCore) {
    log("QUANTARA STARTED SUCCESSFULLY");
    log("");
    log("Windows open:");
    log("  QUANTARA ENGINE");
    log("  QUANTARA WORKER");
    log("  QUANTARA TUNNEL  (copy trycloudflare.com URL to Vercel ENGINE_URL)");
    log("");
    log("Trading control state is unchanged — infrastructure only.");
  } else {
    log("QUANTARA START INCOMPLETE");
    if (!engineUp) log("  ENGINE FAILED — check QUANTARA ENGINE window for errors.");
    if (!workerUp) log("  WORKER FAILED — check QUANTARA WORKER window for errors.");
    if (!tunnelUp) log("  TUNNEL FAILED — check QUANTARA TUNNEL window for errors.");
  }

  log("");
  log(
    `Duplicate protection: ${
      engine.started || worker.started || tunnel.started
        ? "new processes started only where missing"
        : "existing processes reused"
    }`
  );
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
