/** Pure DNS helpers — testable without Node networking. */

export const DEFAULT_POSITIVE_TTL_MS = 60_000;
export const NEGATIVE_CACHE_TTL_MS = 5_000;

const IPV4_RE =
  /^(?:25[0-5]|2[0-4]\d|1?\d?\d)(?:\.(?:25[0-5]|2[0-4]\d|1?\d?\d)){3}$/;

export function isValidIpv4(value) {
  return typeof value === "string" && IPV4_RE.test(value);
}

export function normalizeHostname(hostname) {
  return String(hostname || "")
    .trim()
    .toLowerCase()
    .replace(/\.$/, "");
}

export function hostnameAllowed(hostname, allowedHostnames) {
  const normalized = normalizeHostname(hostname);
  return allowedHostnames.has(normalized);
}

export function parseDoHJson(payload, expectedHostname) {
  const expected = normalizeHostname(expectedHostname);
  if (!payload || typeof payload !== "object") {
    throw new Error("malformed_doh_response");
  }
  if (payload.Status !== 0) {
    throw new Error(`doh_status_${payload.Status}`);
  }
  const answers = Array.isArray(payload.Answer) ? payload.Answer : [];
  const records = [];
  let ttlMs = null;

  for (const answer of answers) {
    if (!answer || answer.type !== 1) continue;
    const name = normalizeHostname(answer.name);
    if (name !== expected) continue;
    const data = String(answer.data || "").trim();
    if (!isValidIpv4(data)) continue;
    records.push(data);
    if (Number.isFinite(answer.TTL) && answer.TTL > 0) {
      const answerTtlMs = answer.TTL * 1000;
      ttlMs = ttlMs == null ? answerTtlMs : Math.min(ttlMs, answerTtlMs);
    }
  }

  const unique = [...new Set(records)];
  if (!unique.length) {
    throw new Error("doh_no_valid_a_records");
  }
  return { addresses: unique, ttlMs: ttlMs ?? DEFAULT_POSITIVE_TTL_MS };
}

export function createCacheEntry(addresses, ttlMs, nowMs = Date.now()) {
  return {
    addresses: [...addresses],
    expiresAt: nowMs + Math.max(1_000, ttlMs),
    nextIndex: 0,
  };
}

export function createNegativeCacheEntry(nowMs = Date.now()) {
  return { expiresAt: nowMs + NEGATIVE_CACHE_TTL_MS };
}

export function cacheState(entry, negativeEntry, nowMs = Date.now()) {
  if (entry && entry.expiresAt > nowMs) return "hit";
  if (negativeEntry && negativeEntry.expiresAt > nowMs) return "negative";
  return "miss";
}

export function pickCachedAddress(entry) {
  if (!entry?.addresses?.length) return null;
  const address = entry.addresses[entry.nextIndex % entry.addresses.length];
  entry.nextIndex = (entry.nextIndex + 1) % entry.addresses.length;
  return address;
}

export function systemLookupSuccess(records) {
  const addresses = (records || [])
    .map((row) => String(row.address || "").trim())
    .filter(isValidIpv4);
  return [...new Set(addresses)];
}

export function isSystemDnsRetryable(code) {
  return code === "ENOTFOUND" || code === "EAI_AGAIN";
}
