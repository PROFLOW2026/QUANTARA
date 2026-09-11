/**
 * Shared helpers for QUANTARA local dev scripts (dev:all / dev:remote).
 */
import { spawn, spawnSync } from "child_process";
import fs from "fs";
import path from "path";
import { loadRepoEnv, getRepoRoot } from "./load-env.cjs";
import {
  findQuantaraEnginePids,
  getEngineListenerPids,
  getProcessCommandLine,
  isProcessAlive,
  isQuantaraEngineCommandLine,
  killProcessTree,
  waitForProcessExit,
} from "./process-utils.mjs";
import { mapWorkerStateToLauncher, runWorkerStateAction } from "./worker-state-client.mjs";

const ROOT = getRepoRoot();
const ENGINE = path.join(ROOT, "apps", "engine");
const ENV_FILE = path.join(ROOT, ".env");
const python = process.platform === "win32" ? "python" : "python3";
const npmCmd = process.platform === "win32" ? "npm.cmd" : "npm";

export function prepareDevEnv() {
  loadRepoEnv(ENV_FILE);
  if (!fs.existsSync(ENV_FILE)) {
    console.error("Missing .env — copy .env.example to .env and fill in secrets.");
    process.exit(1);
  }
  return { ROOT, ENGINE, ENV_FILE };
}

export function engineService() {
  const host = (process.env.ENGINE_HOST || "127.0.0.1").trim();
  const port = (process.env.ENGINE_PORT || "8000").trim();
  return {
    name: "engine",
    label: "ENGINE",
    color: "\x1b[34m",
    cwd: ENGINE,
    command: python,
    args: ["-m", "uvicorn", "main:app", "--host", host, "--port", port],
  };
}

export async function isEngineRunning() {
  const port = (process.env.ENGINE_PORT || "8000").trim();
  const apiKey = (process.env.QUANTARA_API_KEY || "dev-api-key").trim();
  try {
    const res = await fetch(`http://127.0.0.1:${port}/api/v1/health`, {
      headers: { "X-API-Key": apiKey },
      signal: AbortSignal.timeout(3000),
    });
    return res.ok;
  } catch {
    return false;
  }
}

export function workerLockPath() {
  return path.join(ROOT, ".quantara-workers.lock");
}

export function getWorkerStateRaw() {
  return runWorkerStateAction(ENGINE, "inspect");
}

export function getWorkerState() {
  return mapWorkerStateToLauncher(getWorkerStateRaw());
}

export function recoverWorkerStateIfNeeded() {
  const before = getWorkerState();
  if (before.running || before.broken) {
    return { recovered: false, before, after: before };
  }
  if (!before.stale && before.status === "MISSING") {
    return { recovered: false, before, after: before };
  }
  const result = runWorkerStateAction(ENGINE, "recover");
  const after = mapWorkerStateToLauncher(result.after || getWorkerStateRaw());
  return { recovered: Boolean(result.recovered), before, after, result };
}

export function stopWorkerProcessesCanonical() {
  return runWorkerStateAction(ENGINE, "stop");
}

export function isEnginePortListening() {
  const port = (process.env.ENGINE_PORT || "8000").trim();
  return getEngineListenerPids(port).length > 0;
}

export function getEngineListenerPid() {
  const port = (process.env.ENGINE_PORT || "8000").trim();
  const pids = getVerifiedEnginePids(port);
  return pids.length ? pids[0] : null;
}

export function getVerifiedEnginePids(port = (process.env.ENGINE_PORT || "8000").trim()) {
  return findQuantaraEnginePids(port).filter((pid) => isProcessAlive(pid));
}

export function isEngineProcessRunning() {
  const pid = getEngineListenerPid();
  if (!pid) return false;
  return isQuantaraEngineCommandLine(getProcessCommandLine(pid));
}

export function isWorkerRunning() {
  return getWorkerState().running;
}

export function isWorkerSafeToStart() {
  return getWorkerState().safeToStart;
}

export function getWorkerRunningPid() {
  return getWorkerState().pid;
}

export function workersService() {
  const state = getWorkerState();
  if (state.running) {
    console.log(
      `[WORKERS] Existing worker scheduler detected (PID ${state.pid}) — not spawning duplicate`
    );
    return null;
  }
  if (state.broken) {
    console.error(
      `[WORKERS] Singleton lock held but owner unclear (${state.detail || state.reason}).`
    );
    console.error("[WORKERS] Run STOP_QUANTARA.bat before starting a new worker.");
    return null;
  }
  const recovery = recoverWorkerStateIfNeeded();
  if (recovery.recovered) {
    console.log(`[WORKERS] Removed stale worker artifacts (${recovery.before.reason})`);
  }
  const after = recovery.after || getWorkerState();
  if (!after.safeToStart) {
    console.error(
      `[WORKERS] Cannot start worker — state=${after.status} reason=${after.reason}`
    );
    return null;
  }
  return {
    name: "workers",
    label: "WORKERS",
    color: "\x1b[35m",
    cwd: ENGINE,
    command: python,
    args: ["-m", "quantara_workers.main"],
  };
}

export function webService() {
  return {
    name: "web",
    label: "WEB",
    color: "\x1b[32m",
    cwd: ROOT,
    command: npmCmd,
    args: ["run", "dev", "--workspace=@quantara/web"],
  };
}

export async function isLocalWebRunning() {
  try {
    const res = await fetch("http://localhost:3000", {
      signal: AbortSignal.timeout(2000),
      redirect: "manual",
    });
    return res.status > 0 && res.status < 500;
  } catch {
    return false;
  }
}

export async function resolveWebService() {
  if (await isLocalWebRunning()) {
    console.log("[WEB] Port 3000 already in use — reusing existing local Web process");
    return null;
  }
  return webService();
}

export function isTunnelRunning() {
  if (process.platform !== "win32") {
    const result = spawnSync("pgrep", ["-f", "cloudflared.*(localhost:8000|quantara-engine|trycloudflare)"], {
      encoding: "utf8",
    });
    return Boolean(result.stdout?.trim());
  }
  const result = spawnSync("tasklist", ["/FI", "IMAGENAME eq cloudflared.exe"], {
    encoding: "utf8",
  });
  return /cloudflared\.exe/i.test(String(result.stdout || ""));
}

export function isStableTunnelConfigured() {
  const configPath = path.resolve(
    ROOT,
    process.env.CLOUDFLARE_TUNNEL_CONFIG || "scripts/cloudflare-tunnel.local.yml"
  );
  return Boolean(getStableEngineUrl() && fs.existsSync(configPath));
}

export function resolveTunnelService(onLine) {
  if (isStableTunnelConfigured()) {
    return namedTunnelService();
  }
  return quickTunnelService(onLine);
}

export function requireStableTunnelService() {
  if (!isStableTunnelConfigured()) {
    console.error("");
    console.error("Stable Cloudflare tunnel is not configured.");
    console.error("");
    console.error("One-time setup (double-click):");
    console.error("  SETUP_STABLE_TUNNEL.bat");
    console.error("");
    console.error("Or:");
    console.error("  npm run setup:tunnel");
    console.error("");
    process.exit(1);
  }
  return namedTunnelService();
}

export function quickTunnelService(onLine) {
  return {
    name: "tunnel",
    label: "TUNNEL",
    color: "\x1b[33m",
    cwd: ROOT,
    command: "cloudflared",
    args: ["tunnel", "--url", "http://localhost:8000"],
    onLine,
  };
}

export function namedTunnelService() {
  const configPath = path.resolve(
    ROOT,
    process.env.CLOUDFLARE_TUNNEL_CONFIG || "scripts/cloudflare-tunnel.local.yml"
  );
  if (!fs.existsSync(configPath)) {
    console.error("");
    console.error("Missing Cloudflare tunnel config:", configPath);
    console.error("");
    console.error("One-time setup (requires domain in Cloudflare):");
    console.error("  npm run setup:tunnel -- --hostname engine.yourdomain.com");
    console.error("");
    console.error("Then add STABLE_ENGINE_URL to .env and run dev:remote again.");
    process.exit(1);
  }

  const tunnelName = (process.env.CLOUDFLARE_TUNNEL_NAME || "quantara-engine").trim();
  return {
    name: "tunnel",
    label: "TUNNEL",
    color: "\x1b[33m",
    cwd: ROOT,
    command: "cloudflared",
    args: ["tunnel", "--config", configPath, "run", tunnelName],
  };
}

const reset = "\x1b[0m";

function prefix(label, color, chunk) {
  return chunk
    .toString()
    .split(/\r?\n/)
    .filter((line) => line.length > 0)
    .map((line) => `${color}[${label}]${reset} ${line}`)
    .join("\n");
}

export function startServices(services) {
  const active = services.filter(Boolean);
  const children = [];

  for (const service of active) {
    const child = spawn(service.command, service.args, {
      cwd: service.cwd,
      env: process.env,
      stdio: ["ignore", "pipe", "pipe"],
      shell: process.platform === "win32",
    });

    const handleOutput = (data) => {
      const text = data.toString();
      if (service.onLine) {
        for (const line of text.split(/\r?\n/)) {
          if (line.trim()) service.onLine(line);
        }
      }
      return text;
    };

    child.stdout.on("data", (data) => {
      process.stdout.write(prefix(service.label, service.color, handleOutput(data)) + "\n");
    });
    child.stderr.on("data", (data) => {
      process.stderr.write(prefix(service.label, service.color, handleOutput(data)) + "\n");
    });
    child.on("exit", (code, signal) => {
      if (code !== 0 && code !== null) {
        console.error(`${service.label} exited with code ${code}`);
      } else if (signal) {
        console.error(`${service.label} stopped (${signal})`);
      }
    });

    children.push(child);
  }

  function shutdown() {
    console.log("\nStopping QUANTARA...");
    for (const child of children) {
      if (process.platform === "win32") {
        spawn("taskkill", ["/pid", String(child.pid), "/f", "/t"], { shell: true });
      } else {
        child.kill("SIGTERM");
      }
    }
    setTimeout(() => process.exit(0), 500);
  }

  process.on("SIGINT", shutdown);
  process.on("SIGTERM", shutdown);

  return { children, shutdown };
}

export function printTunnelBanner(publicUrl) {
  console.log("");
  console.log("=".repeat(72));
  console.log("PUBLIC ENGINE URL — copy to Vercel ENGINE_URL:");
  console.log(publicUrl);
  console.log("");
  console.log("Also set in Vercel: NEXT_PUBLIC_API_KEY = same as QUANTARA_API_KEY");
  console.log("Then Redeploy the Vercel project.");
  console.log("=".repeat(72));
  console.log("");
}

export function printStableEngineBanner(stableUrl) {
  console.log("");
  console.log("=".repeat(72));
  console.log("STABLE ENGINE URL (same every restart):");
  console.log(stableUrl);
  console.log("");
  console.log("Vercel one-time config:");
  console.log(`  NEXT_PUBLIC_ENGINE_URL=${stableUrl}`);
  console.log("  NEXT_PUBLIC_API_KEY=dev-api-key");
  console.log("=".repeat(72));
  console.log("");
}

export function getStableEngineUrl() {
  const raw = (process.env.STABLE_ENGINE_URL || "").trim();
  if (!raw) return null;
  if (raw.startsWith("http://") || raw.startsWith("https://")) return raw.replace(/\/+$/, "");
  return `https://${raw.replace(/\/+$/, "")}`;
}

export function startWindowsTerminal(service) {
  if (!service) return false;
  const runtimeDir = path.join(ROOT, ".quantara-runtime");
  fs.mkdirSync(runtimeDir, { recursive: true });
  const launcherPath = path.join(runtimeDir, `${service.name}-window.cmd`);
  const commandLine = [service.command, ...service.args]
    .map((part) => (/\s/.test(part) ? `"${part}"` : part))
    .join(" ");
  const preLines = service.preLines || [];
  const script = [
    "@echo off",
    `title ${service.label}`,
    `cd /d "${service.cwd}"`,
    "echo.",
    `echo ${service.label}`,
    "echo ========================================",
    ...preLines.map((line) => `echo ${line}`),
    ...(preLines.length ? ["echo."] : []),
    commandLine,
    "echo.",
    "echo Process exited. You can close this window.",
    "pause",
  ].join("\r\n");
  fs.writeFileSync(launcherPath, script, "utf8");
  spawn("cmd.exe", ["/c", "start", service.label, launcherPath], {
    cwd: ROOT,
    env: process.env,
    shell: false,
    detached: true,
    stdio: "ignore",
  }).unref();
  return true;
}

export async function waitForEngine(maxMs = 90000) {
  const start = Date.now();
  while (Date.now() - start < maxMs) {
    if (await isEngineRunning()) return true;
    await new Promise((resolve) => setTimeout(resolve, 1500));
  }
  return false;
}

export async function waitForWorker(maxMs = 30000) {
  const start = Date.now();
  while (Date.now() - start < maxMs) {
    if (isWorkerRunning()) return true;
    await new Promise((resolve) => setTimeout(resolve, 1000));
  }
  return false;
}

export async function waitForTunnel(maxMs = 30000) {
  const start = Date.now();
  while (Date.now() - start < maxMs) {
    if (isTunnelRunning()) return true;
    await new Promise((resolve) => setTimeout(resolve, 1000));
  }
  return false;
}

export async function checkStableTunnelHealth() {
  const stableUrl = getStableEngineUrl();
  if (!stableUrl) return { ok: false, reason: "STABLE_ENGINE_URL not configured" };
  const apiKey = (process.env.QUANTARA_API_KEY || "dev-api-key").trim();
  try {
    const res = await fetch(`${stableUrl}/api/v1/health`, {
      headers: { "X-API-Key": apiKey },
      signal: AbortSignal.timeout(15000),
    });
    if (!res.ok) return { ok: false, reason: `HTTP ${res.status}` };
    return { ok: true, url: stableUrl };
  } catch (err) {
    return { ok: false, reason: err instanceof Error ? err.message : String(err), url: stableUrl };
  }
}

export async function checkProductionEngine() {
  const base = (process.env.QUANTARA_WEB_URL || "https://quantara-psi.vercel.app").replace(/\/+$/, "");
  const apiKey = (process.env.QUANTARA_API_KEY || "dev-api-key").trim();
  try {
    const res = await fetch(`${base}/api/engine/health`, {
      headers: { "X-API-Key": apiKey },
      signal: AbortSignal.timeout(15000),
    });
    if (!res.ok) return { ok: false, reason: `HTTP ${res.status}` };
    return { ok: true };
  } catch (err) {
    return { ok: false, reason: err instanceof Error ? err.message : String(err) };
  }
}
