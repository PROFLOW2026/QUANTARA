import assert from "node:assert/strict";
import test from "node:test";

import {
  cacheState,
  createCacheEntry,
  createNegativeCacheEntry,
  DEFAULT_POSITIVE_TTL_MS,
  hostnameAllowed,
  isSystemDnsRetryable,
  isValidIpv4,
  parseDoHJson,
  pickCachedAddress,
  systemLookupSuccess,
} from "./engine-dns-core.mjs";

test("isValidIpv4 accepts only valid IPv4 addresses", () => {
  assert.equal(isValidIpv4("185.40.234.37"), true);
  assert.equal(isValidIpv4("256.1.1.1"), false);
  assert.equal(isValidIpv4("not-an-ip"), false);
});

test("parseDoHJson extracts A records for expected hostname only", () => {
  const parsed = parseDoHJson(
    {
      Status: 0,
      Answer: [
        { name: "eran.tailc1ac75.ts.net.", type: 1, TTL: 120, data: "185.40.234.37" },
        { name: "eran.tailc1ac75.ts.net.", type: 1, TTL: 120, data: "185.40.234.172" },
        { name: "other.example.com.", type: 1, TTL: 120, data: "1.2.3.4" },
        { name: "eran.tailc1ac75.ts.net.", type: 28, TTL: 120, data: "::1" },
      ],
    },
    "eran.tailc1ac75.ts.net"
  );
  assert.deepEqual(parsed.addresses, ["185.40.234.37", "185.40.234.172"]);
  assert.equal(parsed.ttlMs, 120_000);
});

test("parseDoHJson rejects malformed DoH responses", () => {
  assert.throws(() => parseDoHJson(null, "eran.tailc1ac75.ts.net"), /malformed/);
  assert.throws(
    () => parseDoHJson({ Status: 3, Answer: [] }, "eran.tailc1ac75.ts.net"),
    /doh_status_3/
  );
  assert.throws(
    () =>
      parseDoHJson(
        { Status: 0, Answer: [{ name: "eran.tailc1ac75.ts.net.", type: 1, TTL: 60, data: "bad" }] },
        "eran.tailc1ac75.ts.net"
      ),
    /doh_no_valid_a_records/
  );
});

test("cache hit and expiry behavior", () => {
  const now = 1_000_000;
  const entry = createCacheEntry(["185.40.234.37", "185.40.234.172"], 60_000, now);
  assert.equal(cacheState(entry, null, now + 1), "hit");
  assert.equal(cacheState(entry, null, now + 60_001), "miss");
  assert.equal(pickCachedAddress(entry), "185.40.234.37");
  assert.equal(pickCachedAddress(entry), "185.40.234.172");
  assert.equal(pickCachedAddress(entry), "185.40.234.37");
});

test("negative cache is short-lived", () => {
  const now = 2_000_000;
  const negative = createNegativeCacheEntry(now);
  assert.equal(cacheState(null, negative, now + 1), "negative");
  assert.equal(cacheState(null, negative, now + 6_000), "miss");
});

test("systemLookupSuccess keeps only valid IPv4 records", () => {
  assert.deepEqual(
    systemLookupSuccess([
      { address: "185.40.234.37" },
      { address: "invalid" },
      { address: "185.40.234.172" },
      { address: "185.40.234.37" },
    ]),
    ["185.40.234.37", "185.40.234.172"]
  );
});

test("system DNS retryable codes", () => {
  assert.equal(isSystemDnsRetryable("ENOTFOUND"), true);
  assert.equal(isSystemDnsRetryable("EAI_AGAIN"), true);
  assert.equal(isSystemDnsRetryable("EBUSY"), true);
  assert.equal(isSystemDnsRetryable("EAGAIN"), true);
  assert.equal(isSystemDnsRetryable("ECONNREFUSED"), false);
});

test("hostname allowlist is exact hostname only", () => {
  const allowed = new Set(["eran.tailc1ac75.ts.net"]);
  assert.equal(hostnameAllowed("eran.tailc1ac75.ts.net", allowed), true);
  assert.equal(hostnameAllowed("evil.example.com", allowed), false);
});

test("default positive TTL fallback", () => {
  const entry = createCacheEntry(["185.40.234.37"], DEFAULT_POSITIVE_TTL_MS, 0);
  assert.equal(entry.expiresAt, 60_000);
});
