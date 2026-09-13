import dns from "dns";

/** Tailscale Funnel DNS can return AAAA first; Vercel serverless often fails IPv6 connect. */
dns.setDefaultResultOrder("ipv4first");

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

/** One retry on transient connect failures (not timeouts). */
export async function fetchEngineUpstream(
  target: string,
  init: RequestInit
): Promise<Response> {
  try {
    return await fetch(target, init);
  } catch (first) {
    if (isFetchTimeout(first)) {
      throw first;
    }
    return fetch(target, init);
  }
}
