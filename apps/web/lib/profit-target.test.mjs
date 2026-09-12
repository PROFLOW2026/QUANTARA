import assert from "node:assert/strict";
import test from "node:test";

function formatRiskRewardLabel(ratio) {
  if (ratio == null || !Number.isFinite(ratio) || ratio <= 0) {
    return "unavailable";
  }
  const rounded =
    Math.abs(ratio - Math.round(ratio)) < 0.05 ? String(Math.round(ratio)) : ratio.toFixed(1);
  return `1:${rounded}`;
}

test("formatRiskRewardLabel uses 1:X convention", () => {
  assert.equal(formatRiskRewardLabel(2), "1:2");
  assert.equal(formatRiskRewardLabel(2.04), "1:2");
  assert.equal(formatRiskRewardLabel(null), "unavailable");
});

test("aggregate R:R from dollar sums", () => {
  const totalRisk = 105;
  const totalTarget = 210;
  assert.equal(totalTarget / totalRisk, 2);
  assert.equal(formatRiskRewardLabel(totalTarget / totalRisk), "1:2");
});
