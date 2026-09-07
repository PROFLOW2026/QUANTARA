import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const { loadRepoEnv, getRepoRoot } = require("./load-env.cjs");

export { loadRepoEnv, getRepoRoot };
