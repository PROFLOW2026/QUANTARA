#!/usr/bin/env node
/** Pre-start checks for START_QUANTARA (PostgreSQL + schema; no runtime start). */
import { spawnSync } from "child_process";
import fs from "fs";
import path from "path";
import { loadRepoEnv, getRepoRoot } from "./load-env.cjs";

const ROOT = getRepoRoot();
const ENV_FILE = path.join(ROOT, ".env");
const python = process.platform === "win32" ? "python" : "python3";

loadRepoEnv(ENV_FILE);
if (!fs.existsSync(ENV_FILE)) {
  console.error("FAIL  Missing .env — configure local quantara_prod DATABASE_URL");
  process.exit(1);
}

const result = spawnSync(python, [path.join(ROOT, "scripts", "preflight_local_runtime.py")], {
  cwd: ROOT,
  encoding: "utf8",
  env: process.env,
});

process.stdout.write(result.stdout || "");
process.stderr.write(result.stderr || "");
process.exit(result.status ?? 1);
