#!/usr/bin/env node
/**
 * Start QUANTARA locally: Engine + Workers + Web (skip Web if :3000 already up).
 */
import {
  prepareDevEnv,
  engineService,
  workersService,
  resolveWebService,
  startServices,
} from "./dev-common.mjs";

prepareDevEnv();

console.log("");
console.log("QUANTARA local dev");
console.log("==================");
console.log("Web UI:     http://localhost:3000");
console.log("Engine API: http://localhost:8000/docs");
console.log("Press Ctrl+C to stop all services");
console.log("");

const web = await resolveWebService();
startServices([engineService(), workersService(), web]);
