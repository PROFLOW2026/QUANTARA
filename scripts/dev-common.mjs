/**
 * Shared helpers for QUANTARA local dev scripts (dev:all / dev:remote).
 */
import { spawn } from "child_process";
import fs from "fs";
import path from "path";
import { loadRepoEnv, getRepoRoot } from "./load-env.cjs";

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
  return {
    name: "engine",
    label: "ENGINE",
    color: "\x1b[34m",
    cwd: ENGINE,
    command: python,
    args: ["main.py"],
  };
}

export function workersService() {
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
