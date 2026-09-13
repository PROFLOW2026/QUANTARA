#!/usr/bin/env node
/**
 * DEV ONLY — local + temporary Quick Tunnel (random trycloudflare.com URL).
 * Production Owner workflow uses Tailscale Funnel via START_QUANTARA.bat.
 */
import { spawnSync } from "child_process";
import {
  prepareDevEnv,
  engineService,
  workersService,
  resolveWebService,
  quickTunnelService,
  startServices,
  printTunnelBanner,
} from "./dev-common.mjs";

prepareDevEnv();

const check = spawnSync("cloudflared", ["--version"], {
  shell: process.platform === "win32",
  stdio: "ignore",
});
if (check.status !== 0) {
  console.error("cloudflared not found.");
  console.error("Install: winget install Cloudflare.cloudflared");
  process.exit(1);
}

let publicUrl = null;
const urlPattern = /https:\/\/[a-z0-9-]+\.trycloudflare\.com/i;

function onTunnelLine(line) {
  const match = line.match(urlPattern);
  if (match && !publicUrl) {
    publicUrl = match[0];
    printTunnelBanner(publicUrl);
  }
}

console.log("");
console.log("QUANTARA remote dev (DEV ONLY — Quick Tunnel, URL changes each restart)");
console.log("====================================================================");
console.log("Production: use START_QUANTARA.bat + SETUP_TAILSCALE_FUNNEL.bat instead.");
console.log("Local Web UI:     http://localhost:3000");
console.log("Local Engine API: http://localhost:8000/docs");
console.log("Public Engine URL will appear below once the tunnel is ready.");
console.log("Press Ctrl+C to stop all services");
console.log("");

const web = await resolveWebService();
startServices([
  engineService(),
  workersService(),
  web,
  quickTunnelService(onTunnelLine),
]);
