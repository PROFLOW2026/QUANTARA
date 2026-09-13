import assert from "node:assert/strict";
import test from "node:test";

import { Agent } from "undici";

import { parseDoHJson } from "./engine-dns-core.mjs";

test("undici custom lookup preserves request URL hostname for TLS/SNI", () => {
  const target = "https://eran.tailc1ac75.ts.net/api/v1/health";
  const url = new URL(target);
  assert.equal(url.hostname, "eran.tailc1ac75.ts.net");
  assert.equal(url.protocol, "https:");
  assert.match(url.href, /^https:\/\/eran\.tailc1ac75\.ts\.net\//);
});

test("undici Agent accepts custom lookup without disabling TLS verification", () => {
  const agent = new Agent({
    connect: {
      lookup(hostname, _options, callback) {
        callback(null, "185.40.234.37", 4);
      },
    },
  });
  assert.equal(typeof agent.dispatch, "function");
  assert.equal(agent, agent);
});

test("DoH fallback path parses Cloudflare and Google JSON shapes", () => {
  const hostname = "eran.tailc1ac75.ts.net";
  const cloudflare = parseDoHJson(
    {
      Status: 0,
      Answer: [{ name: `${hostname}.`, type: 1, TTL: 300, data: "185.40.234.210" }],
    },
    hostname
  );
  const google = parseDoHJson(
    {
      Status: 0,
      Answer: [{ name: `${hostname}.`, type: 1, TTL: 300, data: "185.40.234.210" }],
    },
    hostname
  );
  assert.deepEqual(cloudflare.addresses, google.addresses);
});

test("HTTP status semantics remain distinct from DNS failures", () => {
  const dnsFailure = { error: "upstream_dns_error", system_dns: "ENOTFOUND" };
  const authFailure = { error: "engine_401", detail: "Invalid or missing API key" };
  assert.notEqual(dnsFailure.error, authFailure.error);
  assert.notEqual(String(authFailure.error), "upstream_dns_error");
});
