#!/usr/bin/env node
/**
 * Start QUANTARA for local + Vercel remote testing (Quick Tunnel):
 * Engine + Workers + Web + cloudflared --url http://localhost:8000
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
console.log("QUANTARA remote dev (Quick Tunnel — URL changes each restart)");
console.log("==============================================================");
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
