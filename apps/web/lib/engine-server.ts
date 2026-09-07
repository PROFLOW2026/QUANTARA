const LOCAL_ENGINE = "http://localhost:8000";
const PRODUCTION_ENGINE_FALLBACK =
  "https://afternoon-details-occasional-undergraduate.trycloudflare.com";

export const ENGINE_PROXY_TIMEOUT_MS = 10_000;

function isLocalhostUrl(url: string): boolean {
  return /localhost|127\.0\.0\.1/.test(url);
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
    return PRODUCTION_ENGINE_FALLBACK;
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
