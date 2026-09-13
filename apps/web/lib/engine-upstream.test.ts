import assert from "node:assert/strict";
import test from "node:test";

import { resetEngineDnsStatsForTests } from "./engine-dns";
import { resolveServerApiKey } from "./engine-server";
import {
  describeUpstreamFetchError,
  fetchEngineUpstream,
  resetEngineUpstreamAgentForTests,
  setEngineUpstreamFetchOverride,
} from "./engine-upstream";
import { EngineDnsError } from "./engine-dns";

const TARGET = "https://eran.tailc1ac75.ts.net/api/v1/health";

test.beforeEach(() => {
  process.env.QUANTARA_API_KEY = "test-server-key";
  resetEngineDnsStatsForTests();
  resetEngineUpstreamAgentForTests();
  setEngineUpstreamFetchOverride(null);
});

test.afterEach(() => {
  setEngineUpstreamFetchOverride(null);
});

test("API key is forwarded on upstream requests", async () => {
  let capturedKey = "";
  setEngineUpstreamFetchOverride(async (_target, init) => {
    const headers = init.headers as Record<string, string> | Headers | undefined;
    if (headers instanceof Headers) {
      capturedKey = headers.get("X-API-Key") ?? "";
    } else {
      capturedKey = headers?.["X-API-Key"] ?? "";
    }
    return new Response(JSON.stringify({ ok: true }), { status: 200 });
  });

  await fetchEngineUpstream(TARGET, {
    headers: { Accept: "application/json", "X-API-Key": resolveServerApiKey() },
  });

  assert.equal(capturedKey, "test-server-key");
});

test("HTTP 401 is returned as HTTP response, not DNS failure", async () => {
  setEngineUpstreamFetchOverride(async () =>
    new Response("Invalid or missing API key", { status: 401 })
  );

  const response = await fetchEngineUpstream(TARGET, {
    headers: { Accept: "application/json", "X-API-Key": "bad-key" },
  });

  assert.equal(response.status, 401);
});

test("HTTP 5xx is returned as HTTP response, not DNS failure", async () => {
  setEngineUpstreamFetchOverride(async () =>
    new Response("upstream exploded", { status: 503 })
  );

  const response = await fetchEngineUpstream(TARGET, {
    headers: { Accept: "application/json", "X-API-Key": resolveServerApiKey() },
  });

  assert.equal(response.status, 503);
});

test("timeout errors are not retried as DNS failures", async () => {
  let attempts = 0;
  setEngineUpstreamFetchOverride(async () => {
    attempts += 1;
    const error = new Error("The operation was aborted");
    error.name = "TimeoutError";
    throw error;
  });

  await assert.rejects(
    () =>
      fetchEngineUpstream(TARGET, {
        headers: { Accept: "application/json", "X-API-Key": resolveServerApiKey() },
      }),
    /aborted/
  );
  assert.equal(attempts, 1);
});

test("EngineDnsError diagnostic is preserved for proxy 504 mapping", () => {
  const dnsError = new EngineDnsError({
    error: "upstream_dns_error",
    system_dns: "ENOTFOUND",
    doh: "cloudflare:doh_failed",
    hostname: "eran.tailc1ac75.ts.net",
    cache_state: "miss",
  });

  const described = describeUpstreamFetchError(dnsError);
  assert.equal(described.dns?.error, "upstream_dns_error");
  assert.equal(described.dns?.hostname, "eran.tailc1ac75.ts.net");
});

test("request URL hostname remains canonical for TLS/SNI", () => {
  const url = new URL(TARGET);
  assert.equal(url.hostname, "eran.tailc1ac75.ts.net");
  assert.equal(url.protocol, "https:");
});
