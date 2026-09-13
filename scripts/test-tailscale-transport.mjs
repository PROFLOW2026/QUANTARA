import test from "node:test";
import assert from "node:assert/strict";
import {
  normalizePublicBaseUrl,
  normalizeTsNetHost,
  upsertEnvValue,
  inspectTailscaleAccount,
  isTailscaleInstalled,
} from "./tailscale-transport.mjs";

test("normalizeTsNetHost strips trailing dot", () => {
  assert.equal(normalizeTsNetHost("desktop-abc.tail1234.ts.net."), "desktop-abc.tail1234.ts.net");
  assert.equal(normalizeTsNetHost("not-a-ts-host"), null);
});

test("normalizePublicBaseUrl adds https scheme", () => {
  assert.equal(
    normalizePublicBaseUrl("desktop-abc.tail1234.ts.net"),
    "https://desktop-abc.tail1234.ts.net"
  );
  assert.equal(
    normalizePublicBaseUrl("https://desktop-abc.tail1234.ts.net/"),
    "https://desktop-abc.tail1234.ts.net"
  );
});

test("upsertEnvValue replaces existing key", () => {
  const next = upsertEnvValue("A=1\nENGINE_URL=http://old\n", "ENGINE_URL", "https://x.ts.net");
  assert.match(next, /^ENGINE_URL=https:\/\/x\.ts\.net$/m);
  assert.doesNotMatch(next, /http:\/\/old/);
});

test("upsertEnvValue appends missing key", () => {
  const next = upsertEnvValue("A=1\n", "STABLE_ENGINE_URL", "https://x.ts.net");
  assert.match(next, /STABLE_ENGINE_URL=https:\/\/x\.ts\.net/);
});

test("inspectTailscaleAccount reports install state without throwing", () => {
  const account = inspectTailscaleAccount();
  assert.equal(typeof account.installed, "boolean");
  assert.equal(typeof account.loggedIn, "boolean");
  if (isTailscaleInstalled()) {
    assert.equal(account.installed, true);
  }
});
