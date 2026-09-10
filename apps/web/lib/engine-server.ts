const LOCAL_ENGINE = "http://localhost:8000";
const DEFAULT_PROXY_TIMEOUT_MS = 60_000;

function parseProxyTimeoutMs(): number {
  const raw = process.env.ENGINE_PROXY_TIMEOUT_MS?.trim();
  if (!raw) return DEFAULT_PROXY_TIMEOUT_MS;
  const parsed = Number.parseInt(raw, 10);
  return Number.isFinite(parsed) && parsed >= 10_000 ? parsed : DEFAULT_PROXY_TIMEOUT_MS;
}

/** Server-side proxy + browser /api/engine fetch budget. */
export const ENGINE_PROXY_TIMEOUT_MS = parseProxyTimeoutMs();

function isLocalhostUrl(url: string): boolean {
  return /localhost|127\.0\.1/.test(url);
}

/** Server-only: resolve Engine base URL (never localhost on Vercel). */
export function resolveServerEngineUrl(): string {
  const onVercel = process.env.VERCEL === "1";

  const candidates = [
    process.env.ENGINE_URL,
    process.env.STABLE_ENGINE_URL,
    process.env.QUANTARA_ENGINE_TUNNEL_URL,
  ]
    .map((value) => value?.trim().replace(/\/$/, "") ?? "")
    .filter(Boolean);

  for (const url of candidates) {
    if (onVercel && isLocalhostUrl(url)) continue;
    return url;
  }

  if (onVercel) {
    throw new Error(
      "ENGINE_URL (or STABLE_ENGINE_URL / QUANTARA_ENGINE_TUNNEL_URL) must be set on Vercel"
    );
  }

  return LOCAL_ENGINE;
}

/** Server-only: API key forwarded to Engine. */
export function resolveServerApiKey(): string {
  return (
    process.env.QUANTARA_API_KEY ??
    process.env.NEXT_PUBLIC_API_KEY ??
    "dev-api-key"
  );
}
