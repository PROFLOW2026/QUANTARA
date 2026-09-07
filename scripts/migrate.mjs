import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import pg from "pg";
import { loadRepoEnv } from "./load-env.mjs";

loadRepoEnv();

const __dirname = dirname(fileURLToPath(import.meta.url));
const root = join(__dirname, "..");
const migrationPath = join(root, "packages/db/migrations/0001_initial.sql");

const databaseUrl =
  process.env.DIRECT_URL?.trim() ||
  process.env.DATABASE_URL?.trim() ||
  "postgresql://quantara:quantara@localhost:5432/quantara";
async function migrate() {
  const sql = readFileSync(migrationPath, "utf8");
  const client = new pg.Client({ connectionString: databaseUrl });

  try {
    await client.connect();
    await client.query(sql);
    console.log("Migration 0001_initial.sql applied successfully.");
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    if (message.includes("already exists")) {
      console.log("Migration already applied (objects exist). Skipping.");
      return;
    }
    console.error("Migration failed:", message);
    process.exit(1);
  } finally {
    await client.end();
  }
}

migrate();
