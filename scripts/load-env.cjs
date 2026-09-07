/**
 * Load repo-root `.env` into process.env (does not override existing env vars).
 * Used by Next.js, migrate scripts, and Drizzle tooling.
 */
const fs = require("fs");
const path = require("path");

const REPO_ROOT = path.join(__dirname, "..");

function parseEnvFile(content) {
  const values = {};
  for (const rawLine of content.split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line || line.startsWith("#")) continue;
    const eq = line.indexOf("=");
    if (eq === -1) continue;
    const key = line.slice(0, eq).trim();
    let value = line.slice(eq + 1).trim();
    if (
      (value.startsWith('"') && value.endsWith('"')) ||
      (value.startsWith("'") && value.endsWith("'"))
    ) {
      value = value.slice(1, -1);
    }
    values[key] = value;
  }
  return values;
}

function loadRepoEnv(envPath) {
  const file = envPath || path.join(REPO_ROOT, ".env");
  if (!fs.existsSync(file)) {
    return { loaded: false, path: file, keys: [] };
  }

  const parsed = parseEnvFile(fs.readFileSync(file, "utf8"));
  const keys = [];
  for (const [key, value] of Object.entries(parsed)) {
    if (process.env[key] === undefined) {
      process.env[key] = value;
    }
    keys.push(key);
  }
  return { loaded: true, path: file, keys };
}

function getRepoRoot() {
  return REPO_ROOT;
}

module.exports = { loadRepoEnv, getRepoRoot, parseEnvFile };
