import { readFileSync, readdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import pg from "pg";
import { loadRepoEnv } from "./load-env.mjs";

loadRepoEnv();

const __dirname = dirname(fileURLToPath(import.meta.url));
const root = join(__dirname, "..");
const migrationsDir = join(root, "packages/db/migrations");

const databaseUrl =
  process.env.DIRECT_URL?.trim() ||
  process.env.DATABASE_URL?.trim() ||
  "postgresql://quantara:quantara@localhost:5432/quantara";

async function applyMigration(file) {
  const sql = readFileSync(join(migrationsDir, file), "utf8");
  const client = new pg.Client({ connectionString: databaseUrl });
  await client.connect();
  try {
    await client.query(sql);
    console.log(`Migration ${file} applied successfully.`);
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    if (message.includes("already exists")) {
      console.log(`Migration ${file} already applied (objects exist). Skipping.`);
      return;
    }
    throw error;
  } finally {
    await client.end();
  }
}

async function migrate() {
  const files = readdirSync(migrationsDir)
    .filter((f) => f.endsWith(".sql"))
    .sort();

  for (const file of files) {
    try {
      await applyMigration(file);
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      console.error(`Migration ${file} failed:`, message);
      process.exit(1);
    }
  }
}

migrate();
