import { defineConfig } from "drizzle-kit";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
require("../../scripts/load-env.cjs").loadRepoEnv();

export default defineConfig({
  schema: "./schema/index.ts",
  out: "./migrations",
  dialect: "postgresql",
  dbCredentials: {
    url:
      process.env.DIRECT_URL?.trim() ||
      process.env.DATABASE_URL?.trim() ||
      "",
  },
});