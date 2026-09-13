import assert from "node:assert/strict";
import test from "node:test";

import {
  EngineDnsError,
  engineConnectLookup,
  getEngineDnsStats,
  resetEngineDnsStatsForTests,
  setEngineDnsTestAllowedHostname,
  setEngineDnsTestHooks,
  takePendingEngineDnsError,
} from "./engine-dns";

const ENGINE_HOST = "eran.tailc1ac75.ts.net";

function lookupAsync(hostname: string): Promise<{ address: string; family: number }> {
  return new Promise((resolve, reject) => {
    engineConnectLookup(hostname, {}, (error, address, family) => {
      if (error) {
        reject(error);
        return;
      }
      assert.ok(address);
      resolve({ address: String(address), family: family ?? 4 });
    });
  });
}

function systemError(code: string): NodeJS.ErrnoException {
  const error = new Error(code) as NodeJS.ErrnoException;
  error.code = code;
  return error;
}

function dohJson(addresses: string[], ttl = 120) {
  return {
    Status: 0,
    Answer: addresses.map((data) => ({
      name: `${ENGINE_HOST}.`,
      type: 1,
      TTL: ttl,
      data,
    })),
  };
}

test.beforeEach(() => {
  resetEngineDnsStatsForTests();
  setEngineDnsTestAllowedHostname(ENGINE_HOST);
});

test.afterEach(() => {
  takePendingEngineDnsError();
});

test("system DNS success resolves allowed hostname", async () => {
  setEngineDnsTestHooks({
    systemLookup: async () => [{ address: "185.40.234.37", family: 4 }],
  });

  const resolved = await lookupAsync(ENGINE_HOST);
  assert.equal(resolved.address, "185.40.234.37");
  assert.equal(resolved.family, 4);
  assert.equal(getEngineDnsStats().systemDnsSuccesses, 1);
});

test("system DNS ENOTFOUND falls back to DoH", async () => {
  setEngineDnsTestHooks({
    systemLookup: async () => {
      throw systemError("ENOTFOUND");
    },
    dohFetch: async () =>
      new Response(JSON.stringify(dohJson(["185.40.234.172"])), {
        status: 200,
        headers: { "Content-Type": "application/dns-json" },
      }),
  });

  const resolved = await lookupAsync(ENGINE_HOST);
  assert.equal(resolved.address, "185.40.234.172");
  assert.equal(getEngineDnsStats().dohFallbackUses, 1);
});

test("system DNS EAI_AGAIN falls back to DoH", async () => {
  setEngineDnsTestHooks({
    systemLookup: async () => {
      throw systemError("EAI_AGAIN");
    },
    dohFetch: async () =>
      new Response(JSON.stringify(dohJson(["185.40.234.210"])), {
        status: 200,
        headers: { "Content-Type": "application/dns-json" },
      }),
  });

  const resolved = await lookupAsync(ENGINE_HOST);
  assert.equal(resolved.address, "185.40.234.210");
  assert.equal(getEngineDnsStats().dohFallbackUses, 1);
});

test("system DNS EBUSY falls back to DoH (Vercel serverless incident path)", async () => {
  setEngineDnsTestHooks({
    systemLookup: async () => {
      throw systemError("EBUSY");
    },
    dohFetch: async () =>
      new Response(JSON.stringify(dohJson(["185.40.234.37"])), {
        status: 200,
        headers: { "Content-Type": "application/dns-json" },
      }),
  });

  const resolved = await lookupAsync(ENGINE_HOST);
  assert.equal(resolved.address, "185.40.234.37");
  assert.equal(getEngineDnsStats().dohFallbackUses, 1);
  assert.equal(getEngineDnsStats().dnsFailures, 0);
});

test("DNS cache hit avoids repeat system lookup", async () => {
  let systemCalls = 0;
  setEngineDnsTestHooks({
    systemLookup: async () => {
      systemCalls += 1;
      return [{ address: "185.40.234.37", family: 4 }];
    },
  });

  await lookupAsync(ENGINE_HOST);
  await lookupAsync(ENGINE_HOST);
  assert.equal(systemCalls, 1);
  assert.equal(getEngineDnsStats().cacheHits, 1);
});

test("cache expiry triggers fresh resolution", async () => {
  let dohCalls = 0;
  setEngineDnsTestHooks({
    systemLookup: async () => {
      throw systemError("ENOTFOUND");
    },
    dohFetch: async () => {
      dohCalls += 1;
      return new Response(JSON.stringify(dohJson(["185.40.234.37"], 1)), {
        status: 200,
        headers: { "Content-Type": "application/dns-json" },
      });
    },
  });

  await lookupAsync(ENGINE_HOST);
  assert.equal(dohCalls, 1);
  await new Promise((resolve) => setTimeout(resolve, 1_100));
  await lookupAsync(ENGINE_HOST);
  assert.equal(dohCalls, 2);
});

test("multiple A records rotate across lookups", async () => {
  setEngineDnsTestHooks({
    systemLookup: async () => [
      { address: "185.40.234.37", family: 4 },
      { address: "185.40.234.172", family: 4 },
      { address: "185.40.234.210", family: 4 },
    ],
  });

  const first = await lookupAsync(ENGINE_HOST);
  const second = await lookupAsync(ENGINE_HOST);
  const third = await lookupAsync(ENGINE_HOST);
  const fourth = await lookupAsync(ENGINE_HOST);

  assert.notEqual(first.address, second.address);
  assert.notEqual(second.address, third.address);
  assert.equal(fourth.address, first.address);
});

test("both DNS mechanisms fail returns upstream_dns_error diagnostic", async () => {
  setEngineDnsTestHooks({
    systemLookup: async () => {
      throw systemError("ENOTFOUND");
    },
    dohFetch: async () => new Response("bad gateway", { status: 502 }),
  });

  await assert.rejects(() => lookupAsync(ENGINE_HOST), /Unable to resolve Engine hostname/);
  const pending = takePendingEngineDnsError();
  assert.ok(pending instanceof EngineDnsError);
  assert.equal(pending?.diagnostic.error, "upstream_dns_error");
  assert.equal(pending?.diagnostic.system_dns, "ENOTFOUND");
  assert.match(pending?.diagnostic.doh ?? "", /cloudflare_http_502|google_http_502/);
  assert.equal(pending?.diagnostic.hostname, ENGINE_HOST);
  assert.equal(getEngineDnsStats().dnsFailures, 1);
});

test("disallowed hostname is rejected for SSRF protection", async () => {
  setEngineDnsTestHooks({
    systemLookup: async () => [{ address: "1.2.3.4", family: 4 }],
  });

  await assert.rejects(
    () => lookupAsync("evil.example.com"),
    /engine_hostname_not_allowed/
  );
});
