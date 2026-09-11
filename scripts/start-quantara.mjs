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
  isEngineProcessRunning,
  isWorkerRunning,
  isTunnelRunning,
  quickTunnelService,
  startWindowsTerminal,
  waitForEngine,
  waitForWorker,
  waitForTunnel,
  printTunnelBanner,
} from "./dev-common.mjs";

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

async function launchEngine() {
  if ((await isEngineRunning()) || isEngineProcessRunning()) {
    log("[ENGINE] Already running — skipping duplicate start");
    return { ok: true, started: false };
  }
  const service = engineService();
  service.label = "QUANTARA ENGINE";
  startWindowsTerminal(service);
  log("[ENGINE] Starting in new window...");
  const up = await waitForEngine();
  return { ok: up, started: true };
}

async function launchWorker() {
  if (isWorkerRunning()) {
    log("[WORKER] Existing scheduler detected — skipping duplicate start");
    return { ok: true, started: false };
  }
  const service = workersService();
  if (!service) {
    return { ok: isWorkerRunning(), started: false };
  }
  service.label = "QUANTARA WORKER";
  startWindowsTerminal(service);
  log("[WORKER] Starting in new window...");
  const up = await waitForWorker();
  return { ok: up, started: true };
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

  let tunnelUp = isTunnelRunning();
  if (!tunnelUp && tunnel.started) {
    tunnelUp = await waitForTunnel(30000);
  }

  statusLine("Engine", engineUp, engineUp ? "http://127.0.0.1:8000/api/v1/health" : "not responding");
  statusLine("Worker + Scheduler", workerUp, workerUp ? ".quantara-workers.lock active" : "lock not held");
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
