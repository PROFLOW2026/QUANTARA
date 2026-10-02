import assert from "node:assert/strict";
import test from "node:test";

import {
  brokerEquitySummaryMode,
  shadowCapitalSummaryMode,
} from "./home-research-summary";

test("missing shadow capital is unavailable not ready (no fake zero path)", () => {
  assert.equal(shadowCapitalSummaryMode(false, null), "unavailable");
  assert.equal(shadowCapitalSummaryMode(false, undefined), "unavailable");
});

test("missing broker equity after load is unavailable", () => {
  assert.equal(brokerEquitySummaryMode(false, null), "unavailable");
  assert.equal(brokerEquitySummaryMode(true, null), "loading");
});

test("zero broker equity is ready and distinct from unavailable", () => {
  assert.equal(brokerEquitySummaryMode(false, 0), "ready");
});
