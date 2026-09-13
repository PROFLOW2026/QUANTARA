import dns from "dns";
import { lookup } from "dns/promises";

/** Tailscale Funnel DNS can return AAAA first; Vercel serverless often fails IPv6 connect. */
dns.setDefaultResultOrder("ipv4first");

async function warmEngineDns(hostname: string): Promise<void> {
  await lookup(hostname, { family: 4 });
}

function isFetchTimeout(error: unknown): boolean {
  return (
    error instanceof Error &&
    (error.name === "TimeoutError" || error.name === "AbortError")
  );
}

export function describeUpstreamFetchError(error: unknown): {
  message: string;
  cause?: string;
} {
  if (!(error instanceof Error)) {
    return { message: "Failed to reach engine" };
  }

  const nested = error.cause;
  const cause =
    nested instanceof Error
      ? nested.message
      : nested && typeof nested === "object" && "code" in nested
        ? String((nested as NodeJS.ErrnoException).code)
        : nested != null
          ? String(nested)
          : undefined;

  return {
    message: error.message,
    ...(cause ? { cause } : {}),
  };
}

const TRANSIENT_RETRY_DELAY_MS = 150;

/** One retry on transient connect/DNS failures (not timeouts). */
export async function fetchEngineUpstream(
  target: string,
  init: RequestInit
): Promise<Response> {
  const hostname = new URL(target).hostname;

  const attempt = async () => {
    await warmEngineDns(hostname);
    return fetch(target, init);
  };

  try {
    return await attempt();
  } catch (first) {
    if (isFetchTimeout(first)) {
      throw first;
    }
    await new Promise((resolve) => setTimeout(resolve, TRANSIENT_RETRY_DELAY_MS));
    return attempt();
  }
}
