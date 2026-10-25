#!/usr/bin/env node
/**
 * AI Trader (paper_runner.py) process manager.
 *
 * Used as a shared module by start-quantara.mjs and stop-quantara.mjs,
 * AND as a standalone CLI:
 *   node scripts/ai-trader-manager.mjs start
 *   node scripts/ai-trader-manager.mjs stop
 *   node scripts/ai-trader-manager.mjs status
 *
 * Tracks the running process via a PID file.
 * Graceful stop: writes a stop-flag file that paper_runner.py reads on its
 * next 30-second tick, then force-kills only if the process doesn't exit
 * within 40 s.
 *
 * Does NOT connect to any broker, modify Quantara, or touch the database.
 */

import { spawn, spawnSync } from "child_process";
import path from "path";
import fs from "fs";
import { fileURLToPath } from "url";
import { isProcessAlive } from "./process-utils.mjs";

// ── Configuration ─────────────────────────────────────────────────────────────
// Absolute path to the AI Trader folder. Adjust only if you move the folder.
const AI_TRADER_DIR =
  "C:\\Users\\ERAN YOSEF\\Desktop\\final projects\\FINAL-WEB\\day-trader-skill-trial";
const AI_TRADER_SCRIPT  = "paper_runner.py";

// These files live inside the trader/ subfolder (already used by paper_runner.py)
const TRADER_DIR        = path.join(AI_TRADER_DIR, "trader");
const AI_TRADER_PID_FILE  = path.join(TRADER_DIR, "paper-runner.pid");
const AI_TRADER_STOP_FILE = path.join(TRADER_DIR, "paper-runner.stop");
export const AI_TRADER_LOG_FILE  = path.join(TRADER_DIR, "paper-runner.log");

// ── PID helpers ───────────────────────────────────────────────────────────────
export function readAITraderPid() {
  try {
    const raw = fs.readFileSync(AI_TRADER_PID_FILE, "utf8").trim();
    const pid = parseInt(raw, 10);
    return Number.isFinite(pid) && pid > 0 ? pid : null;
  } catch {
    return null;
  }
}

function writePidFile(pid) {
  try {
    fs.mkdirSync(TRADER_DIR, { recursive: true });
    fs.writeFileSync(AI_TRADER_PID_FILE, String(pid), "utf8");
  } catch (e) {
    console.error(`[AI TRADER] Warning: could not write PID file: ${e.message}`);
  }
}

function cleanupControlFiles() {
  for (const fp of [AI_TRADER_PID_FILE, AI_TRADER_STOP_FILE]) {
    try {
      fs.unlinkSync(fp);
    } catch {}
  }
}

export function isAITraderRunning() {
  const pid = readAITraderPid();
  return pid != null && isProcessAlive(pid);
}

// ── Launch ────────────────────────────────────────────────────────────────────
/**
 * Launch paper_runner.py as a background process (detached, logs to file).
 * Returns { ok, started, pid }.
 * If already running, returns { ok: true, started: false, pid }.
 */
export function launchAITrader() {
  // Duplicate-start guard
  if (isAITraderRunning()) {
    const pid = readAITraderPid();
    console.log(`[AI TRADER] Already running PID=${pid} — skipping duplicate start`);
    return { ok: true, started: false, pid };
  }

  // Verify script exists before trying to launch
  const scriptPath = path.join(AI_TRADER_DIR, AI_TRADER_SCRIPT);
  if (!fs.existsSync(scriptPath)) {
    console.error(`[AI TRADER] Script not found: ${scriptPath}`);
    return { ok: false, started: false, pid: null, error: "script not found" };
  }

  // Remove a stale stop flag left from a previous run
  try {
    fs.unlinkSync(AI_TRADER_STOP_FILE);
  } catch {}

  // Open log file for append (stdout + stderr from paper_runner.py go here)
  fs.mkdirSync(TRADER_DIR, { recursive: true });
  const logFd = fs.openSync(AI_TRADER_LOG_FILE, "a");

  let child;
  try {
    child = spawn("python", [AI_TRADER_SCRIPT], {
      cwd: AI_TRADER_DIR,
      detached: true,
      stdio: ["ignore", logFd, logFd],
      windowsHide: true,          // no console pop-up; output goes to log file
      env: { ...process.env, PYTHONIOENCODING: "utf-8" },  // prevent cp1252 UnicodeEncodeError
    });
    child.unref();                // Node.js exits without waiting for Python
  } catch (e) {
    fs.closeSync(logFd);
    console.error(`[AI TRADER] Failed to launch: ${e.message}`);
    return { ok: false, started: false, pid: null, error: e.message };
  }
  fs.closeSync(logFd);

  const pid = child.pid;
  if (!pid) {
    console.error("[AI TRADER] Launch failed — no PID returned");
    return { ok: false, started: false, pid: null, error: "no PID" };
  }

  // Write PID file immediately (Python also writes it on its own startup)
  writePidFile(pid);

  // Give the process ~2 s to start without spawning a visible cmd.exe window
  const sab = new SharedArrayBuffer(4);
  Atomics.wait(new Int32Array(sab), 0, 0, 2000);   // synchronous 2 s sleep — no subprocess
  if (!isProcessAlive(pid)) {
    console.error(
      `[AI TRADER] Process exited immediately (PID=${pid}). ` +
      `Check log: ${AI_TRADER_LOG_FILE}`
    );
    cleanupControlFiles();
    return { ok: false, started: true, pid, error: "exited immediately" };
  }

  console.log(
    `[AI TRADER] Started PID=${pid} — output → trader\\paper-runner.log`
  );
  return { ok: true, started: true, pid };
}

// ── Stop ──────────────────────────────────────────────────────────────────────
/**
 * Stop paper_runner.py gracefully:
 *   1. Write stop-flag file  → paper_runner picks it up within ≤30 s
 *   2. Wait up to 40 s for graceful exit
 *   3. Force-kill only if still alive
 * Never kills all python.exe processes — only the specific PID.
 */
export async function stopAITrader() {
  const pid = readAITraderPid();

  if (!pid) {
    console.log("[AI TRADER] No PID file found — AI Trader not running (or was never started).");
    return;
  }

  if (!isProcessAlive(pid)) {
    console.log(`[AI TRADER] PID=${pid} is no longer alive — already stopped.`);
    cleanupControlFiles();
    return;
  }

  // Step 1: write stop flag (paper_runner.py checks this each loop iteration)
  console.log(`[AI TRADER] Requesting graceful stop (PID=${pid}) via stop flag...`);
  try {
    fs.mkdirSync(TRADER_DIR, { recursive: true });
    fs.writeFileSync(AI_TRADER_STOP_FILE, "", "utf8");
  } catch (e) {
    console.error(`[AI TRADER] Warning: could not write stop flag: ${e.message}`);
  }

  // Step 2: wait up to 40 s (≤30 s loop tick + 10 s margin for save/journal writes)
  // ── IMPORTANT: poll the PID FILE, not a subprocess ─────────────────────────
  // paper_runner.py deletes paper-runner.pid in its finally block on exit.
  // Watching the file avoids spawnSync("tasklist",...) every second — which
  // creates a visible cmd.exe window each time on Windows.
  const deadline = Date.now() + 40_000;
  let exitedGracefully = false;
  while (Date.now() < deadline) {
    let currentPid = null;
    try {
      const raw = fs.readFileSync(AI_TRADER_PID_FILE, "utf8").trim();
      currentPid = parseInt(raw, 10);
    } catch {
      currentPid = null; // file removed → Python exited and cleaned up
    }
    if (currentPid !== pid) {
      exitedGracefully = true;
      break;
    }
    await new Promise((r) => setTimeout(r, 1_000));
  }

  // Step 3: force-kill if graceful stop didn't work
  if (!exitedGracefully && isProcessAlive(pid)) {
    console.log(`[AI TRADER] Graceful stop timeout — force-killing PID=${pid}`);
    spawnSync("taskkill", ["/F", "/PID", String(pid)], {
      encoding: "utf8",
      windowsHide: true,          // no visible cmd.exe window
    });
    await new Promise((r) => setTimeout(r, 2000));
    if (isProcessAlive(pid)) {
      console.log(`[AI TRADER] WARNING: PID=${pid} still alive after force kill — check Task Manager`);
    } else {
      console.log(`[AI TRADER] Force-killed PID=${pid}`);
    }
  } else if (exitedGracefully) {
    console.log(`[AI TRADER] Stopped gracefully (PID=${pid}) — state and journal saved`);
  } else {
    console.log(`[AI TRADER] Process appears stopped (PID=${pid})`);
  }

  cleanupControlFiles();
}

// ── CLI entrypoint ────────────────────────────────────────────────────────────
// Runs only when this file is the direct entry point (not when imported)
const __filename = fileURLToPath(import.meta.url);
if (process.argv[1] === __filename) {
  const cmd = process.argv[2];
  if (cmd === "start") {
    const result = launchAITrader();
    process.exit(result.ok ? 0 : 1);
  } else if (cmd === "stop") {
    await stopAITrader();
    process.exit(0);
  } else if (cmd === "status") {
    const pid = readAITraderPid();
    const running = isAITraderRunning();
    console.log(
      `AI Trader (paper runner): ${running ? `RUNNING  PID=${pid}` : "STOPPED"}`
    );
    process.exit(0);
  } else {
    console.log("Usage: node scripts/ai-trader-manager.mjs [start|stop|status]");
    process.exit(1);
  }
}
