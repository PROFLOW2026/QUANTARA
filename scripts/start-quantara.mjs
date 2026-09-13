#!/usr/bin/env node
/**
 * One-click QUANTARA infrastructure launcher (Windows-friendly).
 * Starts Engine, Worker/Scheduler, and Tailscale Funnel (stable ts.net URL).
 * Does not modify trading state, DB data, or Vercel configuration.
 */
import { spawnSync } from "child_process";
import path from "path";
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
  startWindowsTerminal,
  waitForEngine,
  waitForWorker,
} from "./dev-common.mjs";
import {
  getProcessCommandLine,
  isQuantaraEngineCommandLine,
  killProcessTree,
  waitForProcessExit,
} from "./process-utils.mjs";
import {
  inspectTailscaleAccount,
  isTailscaleInstalled,
  isTailscaleTransportRunning,
  printStableTransportBanner,
  printTailscaleLoginInstruction,
  resolveStableEngineUrl,
  startFunnel,
  waitForPublicEngineHealth,
} from "./tailscale-transport.mjs";

const { ROOT, ENGINE } = prepareDevEnv();
const python = process.platform === "win32" ? "python" : "python3";
const ENGINE_PORT = Number.parseInt(process.env.ENGINE_PORT || "8000", 10) || 8000;
const API_KEY = (process.env.QUANTARA_API_KEY || "dev-api-key").trim();

function log(line = "") {
  console.log(line);
}

function statusLine(label, ok, detail) {
  const mark = ok ? "PASS" : "FAIL";
  console.log(`${label.padEnd(28)} ${mark}${detail ? ` — ${detail}` : ""}`);
}

function ensureTailscaleReady() {
  if (!isTailscaleInstalled()) {
    console.error("Tailscale is not installed.");
    console.error("Install: winget install Tailscale.Tailscale");
    console.error("Then double-click SETUP_TAILSCALE_FUNNEL.bat");
    process.exit(1);
  }
  const account = inspectTailscaleAccount();
  if (!account.loggedIn) {
    printTailscaleLoginInstruction();
    process.exit(1);
  }
  if (!account.funnelAvailable) {
    console.error("");
    console.error("Tailscale Funnel is not available on this tailnet.");
    console.error("Enable MagicDNS + HTTPS certificates at https://login.tailscale.com/admin/dns");
    console.error("Then run SETUP_TAILSCALE_FUNNEL.bat");
    process.exit(1);
  }
  return account;
}

async function clearStaleEngineListeners() {
  if (await isEngineRunning()) return false;
  if (!isEnginePortListening()) return false;
  const pid = getEngineListenerPid();
  if (!pid) return false;
  const cmd = getProcessCommandLine(pid);
  if (isQuantaraEngineCommandLine(cmd)) {
    log(`[ENGINE] Port ${ENGINE_PORT} held by stale engine PID=${pid} — terminating`);
  } else {
    log(`[ENGINE] Port ${ENGINE_PORT} held by non-engine PID=${pid} — terminating`);
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

async function launchTailscaleFunnel(stableUrl) {
  if (isTailscaleTransportRunning(ENGINE_PORT)) {
    log(`[TRANSPORT] Tailscale Funnel already active for port ${ENGINE_PORT}`);
    return { ok: true, started: false, stableUrl };
  }

  log(`[TRANSPORT] Starting Tailscale Funnel on port ${ENGINE_PORT}...`);
  const started = startFunnel(ENGINE_PORT);
  if (!started.ok) {
    log("[TRANSPORT] Funnel start failed — run SETUP_TAILSCALE_FUNNEL.bat");
    if (started.stderr) log(started.stderr);
    return { ok: false, started: true, stableUrl };
  }
  if (started.stdout) log(started.stdout);

  const health = await waitForPublicEngineHealth(stableUrl, API_KEY, 90000);
  return { ok: health.ok, started: true, stableUrl, healthReason: health.reason };
}

async function main() {
  if (process.platform !== "win32") {
    console.error("START_QUANTARA is intended for Windows double-click use.");
    console.error("On this platform, use: npm run dev:remote");
    process.exit(1);
  }

  const account = ensureTailscaleReady();
  const stableUrl = resolveStableEngineUrl() || account.publicBaseUrl;

  log("");
  log("QUANTARA ONE-CLICK STARTER");
  log("=========================");
  log(`Project root: ${ROOT}`);
  log(`Engine dir:   ${ENGINE}`);
  log(`Transport:    Tailscale Funnel`);
  if (stableUrl) log(`Stable URL:   ${stableUrl}`);
  log("");

  log("Preflight (local PostgreSQL + schema)");
  log("-----------------------------------");
  const preflight = spawnSync(python, [path.join(ROOT, "scripts", "preflight_local_runtime.py")], {
    cwd: ROOT,
    encoding: "utf8",
    env: process.env,
  });
  if (preflight.stdout) log(preflight.stdout.trimEnd());
  if (preflight.status !== 0) {
    if (preflight.stderr) log(preflight.stderr.trimEnd());
    log("");
    log("START BLOCKED — fix PostgreSQL / DATABASE_URL / migrations, then retry.");
    log("  Service: postgresql-x64-17 must be Running on Owner PC.");
    log("  Migrations: npm run db:migrate");
    process.exit(1);
  }
  log("");

  const engine = await launchEngine();
  const worker = await launchWorker();
  const transport = stableUrl
    ? await launchTailscaleFunnel(stableUrl)
    : { ok: false, started: false, stableUrl: null };

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

  let transportUp = isTailscaleTransportRunning(ENGINE_PORT);
  let publicHealth = stableUrl
    ? await waitForPublicEngineHealth(stableUrl, API_KEY, transport.started ? 15000 : 5000)
    : { ok: false, reason: "no stable URL" };

  statusLine(
    "Engine (local)",
    engineUp,
    engineUp ? `http://127.0.0.1:${ENGINE_PORT}/api/v1/health` : "health endpoint not responding"
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
    "Tailscale Funnel",
    transportUp,
    transportUp ? stableUrl || "active" : "not configured"
  );
  statusLine(
    "Public REST health",
    publicHealth.ok,
    publicHealth.ok ? stableUrl : publicHealth.reason
  );

  log("");
  const allCore = engineUp && workerUp && transportUp && publicHealth.ok;
  if (allCore) {
    log("QUANTARA STARTED SUCCESSFULLY");
    log("");
    log("Windows open:");
    log("  QUANTARA ENGINE");
    log("  QUANTARA WORKER");
    log("");
    log("Stable public URL (no Vercel update needed after normal restarts):");
    log(`  ${stableUrl}`);
    log("");
    log("Trading control state is unchanged — infrastructure only.");
    if (!process.env.STABLE_ENGINE_URL?.includes(".ts.net")) {
      printStableTransportBanner(stableUrl);
    }
  } else {
    log("QUANTARA START INCOMPLETE");
    if (!engineUp) log("  ENGINE FAILED — check QUANTARA ENGINE window for errors.");
    if (!workerUp) log("  WORKER FAILED — check QUANTARA WORKER window for errors.");
    if (!transportUp) log("  TRANSPORT FAILED — run SETUP_TAILSCALE_FUNNEL.bat");
    if (!publicHealth.ok) log(`  PUBLIC REST FAILED — ${publicHealth.reason}`);
  }

  log("");
  log(
    `Duplicate protection: ${
      engine.started || worker.started || transport.started
        ? "new processes started only where missing"
        : "existing processes reused"
    }`
  );
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
