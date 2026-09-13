import { Agent, fetch as undiciFetch, type RequestInit as UndiciRequestInit } from "undici";

import {
  EngineDnsError,
  engineConnectLookup,
  takePendingEngineDnsError,
} from "./engine-dns";

const TRANSIENT_RETRY_DELAY_MS = 150;

let sharedAgent: Agent | null = null;

function getEngineAgent(): Agent {
  if (!sharedAgent) {
    sharedAgent = new Agent({
      connect: {
        lookup: engineConnectLookup,
        family: 4,
        timeout: 30_000,
      },
      keepAliveTimeout: 10_000,
      keepAliveMaxTimeout: 30_000,
    });
  }
  return sharedAgent;
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
  dns?: EngineDnsError["diagnostic"];
} {
  if (error instanceof EngineDnsError) {
    return {
      message: error.message,
      dns: error.diagnostic,
    };
  }

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

type UpstreamFetchFn = (target: string, init: RequestInit) => Promise<Response>;

let upstreamFetchOverride: UpstreamFetchFn | null = null;

/** Test-only override for upstream fetch behavior. */
export function setEngineUpstreamFetchOverride(fn: UpstreamFetchFn | null): void {
  upstreamFetchOverride = fn;
}

async function performFetch(target: string, init: RequestInit): Promise<Response> {
  if (upstreamFetchOverride) {
    return upstreamFetchOverride(target, init);
  }
  const dispatcher = getEngineAgent();
  const { signal, ...rest } = init;
  const undiciInit = {
    ...rest,
    dispatcher,
    ...(signal ? { signal } : {}),
  } as UndiciRequestInit;
  return undiciFetch(target, undiciInit) as unknown as Response;
}

/** One retry on transient connect/DNS failures (not timeouts or HTTP errors). */
export async function fetchEngineUpstream(
  target: string,
  init: RequestInit
): Promise<Response> {
  try {
    return await performFetch(target, init);
  } catch (first) {
    const dnsError = takePendingEngineDnsError();
    if (dnsError) {
      throw dnsError;
    }
    if (isFetchTimeout(first)) {
      throw first;
    }
    await new Promise((resolve) => setTimeout(resolve, TRANSIENT_RETRY_DELAY_MS));
    try {
      return await performFetch(target, init);
    } catch (second) {
      const retryDnsError = takePendingEngineDnsError();
      if (retryDnsError) {
        throw retryDnsError;
      }
      throw second;
    }
  }
}

/** Test-only hook to reset the shared undici agent between tests. */
export function resetEngineUpstreamAgentForTests(): void {
  sharedAgent = null;
}
