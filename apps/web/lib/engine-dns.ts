import dns from "node:dns";
import type { LookupFunction } from "node:net";
import { lookup as systemLookup } from "node:dns/promises";

import {
  cacheState,
  createCacheEntry,
  createNegativeCacheEntry,
  DEFAULT_POSITIVE_TTL_MS,
  hostnameAllowed,
  isSystemDnsRetryable,
  isValidIpv4,
  normalizeHostname,
  parseDoHJson,
  pickCachedAddress,
  systemLookupSuccess,
} from "./engine-dns-core.mjs";
import { resolveServerEngineUrl } from "./engine-server";

dns.setDefaultResultOrder("ipv4first");

const DOH_PROVIDERS = [
  {
    name: "cloudflare",
    url: (hostname: string) =>
      `https://cloudflare-dns.com/dns-query?name=${encodeURIComponent(hostname)}&type=A`,
    accept: "application/dns-json",
  },
  {
    name: "google",
    url: (hostname: string) =>
      `https://dns.google/resolve?name=${encodeURIComponent(hostname)}&type=A`,
    accept: "application/dns-json",
  },
] as const;

type CacheEntry = {
  addresses: string[];
  expiresAt: number;
  nextIndex: number;
};

type NegativeCacheEntry = { expiresAt: number };

export type EngineDnsStats = {
  systemDnsSuccesses: number;
  dohFallbackUses: number;
  cacheHits: number;
  dnsFailures: number;
};

type EngineDnsGlobal = typeof globalThis & {
  __quantaraEngineDnsStats?: EngineDnsStats;
  __quantaraEngineDnsPositiveCache?: Map<string, CacheEntry>;
  __quantaraEngineDnsNegativeCache?: Map<string, NegativeCacheEntry>;
};

const engineDnsGlobal = globalThis as EngineDnsGlobal;

const stats: EngineDnsStats =
  engineDnsGlobal.__quantaraEngineDnsStats ??
  (engineDnsGlobal.__quantaraEngineDnsStats = {
    systemDnsSuccesses: 0,
    dohFallbackUses: 0,
    cacheHits: 0,
    dnsFailures: 0,
  });

const positiveCache =
  engineDnsGlobal.__quantaraEngineDnsPositiveCache ??
  (engineDnsGlobal.__quantaraEngineDnsPositiveCache = new Map<string, CacheEntry>());
const negativeCache =
  engineDnsGlobal.__quantaraEngineDnsNegativeCache ??
  (engineDnsGlobal.__quantaraEngineDnsNegativeCache = new Map<string, NegativeCacheEntry>());

let pendingDnsError: EngineDnsError | null = null;

export type EngineDnsTestHooks = {
  systemLookup?: typeof systemLookup;
  dohFetch?: typeof fetch;
};

let engineDnsTestHooks: EngineDnsTestHooks | null = null;
let testAllowedHostnameOverride: string | null = null;

/** Test-only injection for resolver integration tests. */
export function setEngineDnsTestHooks(hooks: EngineDnsTestHooks | null): void {
  engineDnsTestHooks = hooks;
}

/** Test-only allowlist override so tests do not depend on import-time env. */
export function setEngineDnsTestAllowedHostname(hostname: string | null): void {
  testAllowedHostnameOverride = hostname ? normalizeHostname(hostname) : null;
}

export function takePendingEngineDnsError(): EngineDnsError | null {
  const error = pendingDnsError;
  pendingDnsError = null;
  return error;
}

export class EngineDnsError extends Error {
  readonly diagnostic: {
    error: "upstream_dns_error";
    system_dns: string;
    doh: string;
    hostname: string;
    cache_state: string;
  };

  constructor(diagnostic: EngineDnsError["diagnostic"]) {
    super(
      `Unable to resolve Engine hostname ${diagnostic.hostname} (${diagnostic.system_dns}; ${diagnostic.doh})`
    );
    this.name = "EngineDnsError";
    this.diagnostic = diagnostic;
  }
}

export function getEngineDnsStats(): EngineDnsStats {
  return { ...stats };
}

export function resetEngineDnsStatsForTests(): void {
  stats.systemDnsSuccesses = 0;
  stats.dohFallbackUses = 0;
  stats.cacheHits = 0;
  stats.dnsFailures = 0;
  pendingDnsError = null;
  positiveCache.clear();
  negativeCache.clear();
  engineDnsTestHooks = null;
  testAllowedHostnameOverride = null;
}

function allowedEngineHostnames(): Set<string> {
  if (testAllowedHostnameOverride) {
    return new Set([testAllowedHostnameOverride]);
  }
  try {
    const hostname = normalizeHostname(new URL(resolveServerEngineUrl()).hostname);
    return new Set([hostname]);
  } catch {
    return new Set();
  }
}

function assertAllowedHostname(hostname: string): string {
  const normalized = normalizeHostname(hostname);
  const allowed = allowedEngineHostnames();
  if (!allowed.size || !hostnameAllowed(normalized, allowed)) {
    throw new Error(`engine_hostname_not_allowed:${normalized}`);
  }
  return normalized;
}

async function querySystemDns(hostname: string): Promise<{ addresses: string[]; ttlMs: number }> {
  const lookupFn = engineDnsTestHooks?.systemLookup ?? systemLookup;
  const records = await lookupFn(hostname, { all: true, verbatim: true });
  const rows = Array.isArray(records) ? records : [records];
  const ipv4 = rows
    .filter((row) => row.family === 4)
    .map((row) => String(row.address))
    .filter(isValidIpv4);
  const addresses = systemLookupSuccess(ipv4.map((address) => ({ address })));
  if (!addresses.length) {
    const err = new Error(`system_dns_no_ipv4:${hostname}`) as NodeJS.ErrnoException;
    err.code = "ENOTFOUND";
    throw err;
  }
  return { addresses, ttlMs: DEFAULT_POSITIVE_TTL_MS };
}

async function queryDoH(hostname: string): Promise<{ addresses: string[]; ttlMs: number; provider: string }> {
  let lastError = "doh_not_attempted";
  for (const provider of DOH_PROVIDERS) {
    try {
      const fetchFn = engineDnsTestHooks?.dohFetch ?? fetch;
      const response = await fetchFn(provider.url(hostname), {
        headers: { Accept: provider.accept },
        cache: "no-store",
        signal: AbortSignal.timeout(5_000),
      });
      if (!response.ok) {
        lastError = `${provider.name}_http_${response.status}`;
        continue;
      }
      const payload = await response.json();
      const parsed = parseDoHJson(payload, hostname);
      return { ...parsed, provider: provider.name };
    } catch (error) {
      lastError =
        error instanceof Error ? `${provider.name}:${error.message}` : `${provider.name}:failed`;
    }
  }
  const err = new Error(lastError) as NodeJS.ErrnoException;
  err.code = "ENOTFOUND";
  throw err;
}

async function resolveAddresses(hostname: string): Promise<{
  address: string;
  source: "cache" | "system" | "doh";
}> {
  const normalized = assertAllowedHostname(hostname);
  const now = Date.now();
  const cached = positiveCache.get(normalized);
  const negative = negativeCache.get(normalized);
  const state = cacheState(cached, negative, now);

  if (state === "hit" && cached) {
    const address = pickCachedAddress(cached);
    if (address) {
      stats.cacheHits += 1;
      return { address, source: "cache" };
    }
  }
  if (state === "negative") {
    stats.dnsFailures += 1;
    throw new EngineDnsError({
      error: "upstream_dns_error",
      system_dns: "negative_cache",
      doh: "skipped",
      hostname: normalized,
      cache_state: "negative",
    });
  }

  let addresses: string[] = [];
  let ttlMs = DEFAULT_POSITIVE_TTL_MS;
  let systemDnsResult = "ok";
  let dohResult = "not_used";

  try {
    const system = await querySystemDns(normalized);
    addresses = system.addresses;
    ttlMs = system.ttlMs;
    stats.systemDnsSuccesses += 1;
  } catch (error) {
    const code =
      error && typeof error === "object" && "code" in error
        ? String((error as NodeJS.ErrnoException).code)
        : "UNKNOWN";
    systemDnsResult = code;
    if (!isSystemDnsRetryable(code)) {
      stats.dnsFailures += 1;
      throw new EngineDnsError({
        error: "upstream_dns_error",
        system_dns: systemDnsResult,
        doh: "not_attempted",
        hostname: normalized,
        cache_state: state,
      });
    }

    try {
      const doh = await queryDoH(normalized);
      addresses = doh.addresses;
      ttlMs = doh.ttlMs;
      dohResult = doh.provider;
      stats.dohFallbackUses += 1;
    } catch (dohError) {
      dohResult =
        dohError instanceof Error ? dohError.message : "doh_failed";
      negativeCache.set(normalized, createNegativeCacheEntry(now));
      stats.dnsFailures += 1;
      throw new EngineDnsError({
        error: "upstream_dns_error",
        system_dns: systemDnsResult,
        doh: dohResult,
        hostname: normalized,
        cache_state: state,
      });
    }
  }

  const entry = createCacheEntry(addresses, ttlMs, now);
  positiveCache.set(normalized, entry);
  negativeCache.delete(normalized);
  const address = pickCachedAddress(entry);
  if (!address) {
    stats.dnsFailures += 1;
    throw new EngineDnsError({
      error: "upstream_dns_error",
      system_dns: systemDnsResult,
      doh: dohResult,
      hostname: normalized,
      cache_state: "empty",
    });
  }

  return {
    address,
    source: dohResult === "not_used" ? "system" : "doh",
  };
}

/** Custom lookup used by undici — controls the TCP/TLS connect target. */
export const engineConnectLookup: LookupFunction = (hostname, _options, callback) => {
  resolveAddresses(hostname)
    .then(({ address }) => callback(null, address, 4))
    .catch((error) => {
      if (error instanceof EngineDnsError) {
        pendingDnsError = error;
        const err = new Error(error.message) as NodeJS.ErrnoException;
        err.code = "ENOTFOUND";
        callback(err, "", 4);
        return;
      }
      callback(error as NodeJS.ErrnoException, "", 4);
    });
};

export async function prefetchEngineDns(hostname: string): Promise<void> {
  await resolveAddresses(hostname);
}
