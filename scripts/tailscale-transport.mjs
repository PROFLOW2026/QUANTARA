/**
 * Tailscale Funnel transport — stable public HTTPS for Engine :8000 ($0).
 */
import fs from "fs";
import { spawnSync } from "child_process";

const DEFAULT_WIN_CLI = "C:\\Program Files\\Tailscale\\tailscale.exe";

export function resolveTailscaleCli() {
  const fromEnv = (process.env.TAILSCALE_CLI || "").trim();
  if (fromEnv) return fromEnv;
  if (process.platform === "win32" && fs.existsSync(DEFAULT_WIN_CLI)) {
    return DEFAULT_WIN_CLI;
  }
  return "tailscale";
}

export function runTailscale(args, options = {}) {
  const cli = resolveTailscaleCli();
  return spawnSync(cli, args, {
    encoding: "utf8",
    timeout: options.timeout ?? 30000,
    shell: false,
  });
}

export function isTailscaleInstalled() {
  const result = runTailscale(["version"], { timeout: 8000 });
  return result.status === 0 && /^\d+\.\d+/m.test(String(result.stdout || ""));
}

export function parseTailscaleStatusJson(stdout) {
  try {
    return JSON.parse(stdout || "{}");
  } catch {
    return null;
  }
}

export function inspectTailscaleAccount() {
  if (!isTailscaleInstalled()) {
    return {
      installed: false,
      loggedIn: false,
      backendState: "not_installed",
      magicDns: false,
      https: false,
      funnelAvailable: false,
      dnsName: null,
      publicBaseUrl: null,
      detail: "Tailscale CLI not found",
    };
  }

  const result = runTailscale(["status", "--json"]);
  const combined = `${result.stdout || ""}\n${result.stderr || ""}`.trim();
  if (result.status !== 0 && /logged out/i.test(combined)) {
    return {
      installed: true,
      loggedIn: false,
      backendState: "LoggedOut",
      magicDns: false,
      https: false,
      funnelAvailable: false,
      dnsName: null,
      publicBaseUrl: null,
      detail: "Logged out",
    };
  }

  const data = parseTailscaleStatusJson(result.stdout);
  if (!data) {
    return {
      installed: true,
      loggedIn: false,
      backendState: "unknown",
      magicDns: false,
      https: false,
      funnelAvailable: false,
      dnsName: null,
      publicBaseUrl: null,
      detail: combined || "Unable to parse tailscale status",
    };
  }

  const backendState = String(data.BackendState || "unknown");
  const loggedIn = backendState === "Running";
  const dnsName = normalizeTsNetHost(data.Self?.DNSName);
  const magicDns = Boolean(dnsName);
  const certDomains = Array.isArray(data.CertDomains) ? data.CertDomains : [];
  const https = certDomains.length > 0 || magicDns;
  const funnelAvailable = loggedIn && magicDns && https;

  return {
    installed: true,
    loggedIn,
    backendState,
    magicDns,
    https,
    funnelAvailable,
    dnsName,
    publicBaseUrl: dnsName ? `https://${dnsName}` : null,
    certDomains,
    detail: loggedIn ? "Running" : backendState,
  };
}

export function normalizeTsNetHost(raw) {
  if (!raw || typeof raw !== "string") return null;
  const trimmed = raw.trim().replace(/\.$/, "");
  if (!/\.ts\.net$/i.test(trimmed)) return null;
  return trimmed;
}

export function normalizePublicBaseUrl(raw) {
  if (!raw || typeof raw !== "string") return null;
  const trimmed = raw.trim().replace(/\/+$/, "");
  if (!trimmed) return null;
  if (trimmed.startsWith("http://") || trimmed.startsWith("https://")) return trimmed;
  return `https://${trimmed}`;
}

export function getConfiguredStableEngineUrl() {
  const candidates = [
    process.env.STABLE_ENGINE_URL,
    process.env.ENGINE_URL,
    process.env.QUANTARA_ENGINE_TUNNEL_URL,
  ];
  for (const value of candidates) {
    const url = normalizePublicBaseUrl(value);
    if (url && /\.ts\.net$/i.test(new URL(url).hostname)) return url;
  }
  return null;
}

export function resolveStableEngineUrl() {
  return getConfiguredStableEngineUrl() || inspectTailscaleAccount().publicBaseUrl;
}

export function parseFunnelStatusJson(stdout) {
  try {
    const data = JSON.parse(stdout || "{}");
    return data && typeof data === "object" ? data : {};
  } catch {
    return {};
  }
}

export function funnelStatusText() {
  const result = runTailscale(["funnel", "status"]);
  return {
    ok: result.status === 0,
    stdout: String(result.stdout || "").trim(),
    stderr: String(result.stderr || "").trim(),
  };
}

export function isFunnelConfiguredForPort(port = 8000) {
  const jsonResult = runTailscale(["funnel", "status", "--json"]);
  if (jsonResult.status !== 0) return false;
  const status = parseFunnelStatusJson(jsonResult.stdout);
  const blob = JSON.stringify(status);
  if (!blob || blob === "{}") return false;
  const portStr = String(port);
  return (
    blob.includes(`"${portStr}"`) ||
    blob.includes(`:${portStr}`) ||
    blob.includes(`127.0.0.1:${portStr}`) ||
    blob.includes(`localhost:${portStr}`)
  );
}

export function startFunnel(port = 8000) {
  const args = ["funnel", "--bg", "--yes", String(port)];
  const result = runTailscale(args, { timeout: 120000 });
  return {
    ok: result.status === 0,
    stdout: String(result.stdout || "").trim(),
    stderr: String(result.stderr || "").trim(),
    status: result.status,
  };
}

export function stopFunnel() {
  const result = runTailscale(["funnel", "reset"], { timeout: 30000 });
  return {
    ok: result.status === 0,
    stdout: String(result.stdout || "").trim(),
    stderr: String(result.stderr || "").trim(),
  };
}

export function isTailscaleTransportRunning(port = 8000) {
  if (!isTailscaleInstalled()) return false;
  const account = inspectTailscaleAccount();
  if (!account.loggedIn) return false;
  return isFunnelConfiguredForPort(port);
}

export async function checkPublicEngineHealth(baseUrl, apiKey = "dev-api-key") {
  const url = normalizePublicBaseUrl(baseUrl);
  if (!url) return { ok: false, reason: "missing base URL" };
  try {
    const res = await fetch(`${url}/api/v1/health`, {
      headers: { "X-API-Key": apiKey },
      signal: AbortSignal.timeout(15000),
    });
    if (!res.ok) return { ok: false, reason: `HTTP ${res.status}`, url };
    return { ok: true, url };
  } catch (err) {
    return {
      ok: false,
      reason: err instanceof Error ? err.message : String(err),
      url,
    };
  }
}

export async function waitForPublicEngineHealth(baseUrl, apiKey, maxMs = 120000) {
  const start = Date.now();
  let last = { ok: false, reason: "waiting" };
  while (Date.now() - start < maxMs) {
    last = await checkPublicEngineHealth(baseUrl, apiKey);
    if (last.ok) return last;
    await new Promise((resolve) => setTimeout(resolve, 3000));
  }
  return last;
}

export function upsertEnvValue(envText, key, value) {
  const line = `${key}=${value}`;
  const pattern = new RegExp(`^${key}=.*$`, "m");
  if (pattern.test(envText)) {
    return envText.replace(pattern, line);
  }
  const suffix = envText.endsWith("\n") || envText.length === 0 ? "" : "\n";
  return `${envText}${suffix}${line}\n`;
}

export function persistStableEngineUrl(envPath, url) {
  const normalized = normalizePublicBaseUrl(url);
  if (!normalized) {
    throw new Error("Invalid stable Engine URL");
  }
  const previous = fs.existsSync(envPath) ? fs.readFileSync(envPath, "utf8") : "";
  let next = upsertEnvValue(previous, "STABLE_ENGINE_URL", normalized);
  next = upsertEnvValue(next, "ENGINE_URL", normalized);
  fs.writeFileSync(envPath, next, "utf8");
  return normalized;
}

export function printTailscaleLoginInstruction() {
  console.log("");
  console.log("=".repeat(72));
  console.log("TAILSCALE LOGIN REQUIRED (one time)");
  console.log("");
  console.log("1. Open Tailscale from the Windows Start Menu");
  console.log("2. Sign in with your Tailscale account");
  console.log("3. Double-click SETUP_TAILSCALE_FUNNEL.bat");
  console.log("=".repeat(72));
  console.log("");
}

export function printStableTransportBanner(stableUrl) {
  console.log("");
  console.log("=".repeat(72));
  console.log("STABLE ENGINE URL (same after every restart):");
  console.log(stableUrl);
  console.log("");
  console.log("One-time Vercel Production config:");
  console.log(`  ENGINE_URL=${stableUrl}`);
  console.log("  (stream-base + REST both use ENGINE_URL — no separate SSE env needed)");
  console.log("");
  console.log("After the first Vercel redeploy, normal Engine/PC restarts do NOT");
  console.log("require Vercel env changes or redeploys.");
  console.log("=".repeat(72));
  console.log("");
}
