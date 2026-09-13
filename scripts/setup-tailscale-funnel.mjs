#!/usr/bin/env node
/**
 * One-time Tailscale Funnel setup for QUANTARA Engine (:8000).
 * Produces a stable https://<machine>.<tailnet>.ts.net URL ($0).
 */
import path from "path";
import { prepareDevEnv } from "./dev-common.mjs";
import {
  inspectTailscaleAccount,
  isTailscaleInstalled,
  persistStableEngineUrl,
  printStableTransportBanner,
  printTailscaleLoginInstruction,
  resolveStableEngineUrl,
  startFunnel,
  stopFunnel,
  waitForPublicEngineHealth,
} from "./tailscale-transport.mjs";

const { ROOT, ENV_FILE } = prepareDevEnv();
const ENGINE_PORT = Number.parseInt(process.env.ENGINE_PORT || "8000", 10) || 8000;
const API_KEY = (process.env.QUANTARA_API_KEY || "dev-api-key").trim();

function reportAccount(label, account) {
  console.log(`${label.padEnd(28)} ${account.installed ? "YES" : "NO"}`);
  if (!account.installed) return;
  console.log(`${"Tailscale logged in".padEnd(28)} ${account.loggedIn ? "YES" : "NO"}`);
  console.log(`${"MagicDNS".padEnd(28)} ${account.magicDns ? "enabled" : "disabled"}`);
  console.log(`${"HTTPS".padEnd(28)} ${account.https ? "enabled" : "disabled"}`);
  console.log(`${"Funnel capability".padEnd(28)} ${account.funnelAvailable ? "available" : "not available"}`);
  if (account.dnsName) {
    console.log(`${"Machine DNS".padEnd(28)} ${account.dnsName}`);
  }
}

async function main() {
  console.log("");
  console.log("QUANTARA — TAILSCALE FUNNEL SETUP");
  console.log("=================================");
  console.log(`Project root: ${ROOT}`);
  console.log("");

  const account = inspectTailscaleAccount();
  reportAccount("Tailscale installed", account);

  if (!isTailscaleInstalled()) {
    console.log("");
    console.log("Install Tailscale:");
    console.log("  winget install Tailscale.Tailscale");
    process.exit(1);
  }

  if (!account.loggedIn) {
    printTailscaleLoginInstruction();
    process.exit(1);
  }

  if (!account.funnelAvailable) {
    console.log("");
    console.log("Tailscale Funnel prerequisites missing.");
    console.log("In https://login.tailscale.com/admin/dns enable MagicDNS and HTTPS certificates.");
    console.log("Then retry this setup.");
    process.exit(1);
  }

  console.log("");
  console.log(`Enabling Funnel for http://127.0.0.1:${ENGINE_PORT} ...`);
  stopFunnel();
  const started = startFunnel(ENGINE_PORT);
  if (!started.ok) {
    console.error("Funnel start failed.");
    if (started.stdout) console.error(started.stdout);
    if (started.stderr) console.error(started.stderr);
    process.exit(1);
  }
  if (started.stdout) console.log(started.stdout);
  if (started.stderr) console.log(started.stderr);

  const stableUrl = resolveStableEngineUrl();
  if (!stableUrl) {
    console.error("Could not resolve stable ts.net URL from Tailscale status.");
    process.exit(1);
  }

  persistStableEngineUrl(ENV_FILE, stableUrl);
  console.log(`Updated ${path.relative(ROOT, ENV_FILE)}:`);
  console.log(`  STABLE_ENGINE_URL=${stableUrl}`);
  console.log(`  ENGINE_URL=${stableUrl}`);

  console.log("");
  console.log("Waiting for public /api/v1/health ...");
  const health = await waitForPublicEngineHealth(stableUrl, API_KEY, 120000);
  console.log(
    `${"Public REST health".padEnd(28)} ${health.ok ? "PASS" : "FAIL"}${health.ok ? "" : ` — ${health.reason}`}`
  );

  printStableTransportBanner(stableUrl);

  if (!health.ok) {
    console.log("Funnel URL is configured but Engine health did not pass yet.");
    console.log("Start Engine with START_QUANTARA.bat, then re-run this setup to verify.");
    process.exit(1);
  }

  console.log("Setup complete. Run START_QUANTARA.bat for normal daily use.");
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
