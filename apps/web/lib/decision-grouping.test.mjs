import assert from "node:assert/strict";
import test from "node:test";

function resolveDecisionType(type, metadata) {
  const layer = metadata?.layer;
  if (type === "risk_denied" && layer === "broker_execution") return "broker_rejected";
  if (type === "risk_denied" && layer === "broker_capability") return "broker_capability_denied";
  return type;
}

function researchDecisionGroupKey(decision) {
  const candle = decision.candle_time ?? decision.timestamp ?? "";
  const timeframe = decision.timeframe ?? decision.signal?.timeframe ?? "";
  const robot = decision.robot_label ?? decision.strategy_slug ?? decision.strategy_name ?? "";
  const message = (decision.message ?? "").trim();
  const layer = String(decision.metadata?.layer ?? "");
  const brokerReason = String(decision.metadata?.broker_reason ?? "");
  const resolvedType = resolveDecisionType(decision.decision_type, decision.metadata);
  return [
    decision.instrument ?? "",
    robot,
    timeframe,
    candle,
    resolvedType,
    message,
    layer,
    brokerReason,
    String(decision.entry_signal ?? ""),
    String(decision.position_open ?? ""),
  ].join("\u0000");
}

function groupResearchDecisions(decisions) {
  const buckets = new Map();
  for (const decision of decisions) {
    const key = researchDecisionGroupKey(decision);
    const list = buckets.get(key) ?? [];
    list.push(decision);
    buckets.set(key, list);
  }
  return [...buckets.entries()].map(([key, members]) => ({ key, members }));
}

const base = {
  instrument: "ETH/USD",
  robot_label: "Robot B",
  strategy_slug: "opening-range-breakout",
  timeframe: "5m",
  candle_time: "2026-09-13T01:20:00+03:00",
  timestamp: "2026-09-13T01:20:00+03:00",
  decision_type: "no_setup",
  message: "opening_range_not_complete",
  metadata: {},
};

test("groups five identical tier decisions into one bucket", () => {
  const tiers = ["very_conservative", "conservative", "balanced", "aggressive", "very_aggressive"];
  const decisions = tiers.map((risk_slug, i) => ({
    ...base,
    id: `d-${i}`,
    risk_slug,
    portfolio_name: `ETH tier ${i}`,
  }));
  const groups = groupResearchDecisions(decisions);
  assert.equal(groups.length, 1);
  assert.equal(groups[0].members.length, 5);
});

test("keeps different robots separate", () => {
  const decisions = [
    { ...base, id: "a", robot_label: "Robot A" },
    { ...base, id: "b", robot_label: "Robot B" },
  ];
  const groups = groupResearchDecisions(decisions);
  assert.equal(groups.length, 2);
});

test("keeps mixed outcomes separate", () => {
  const decisions = [
    { ...base, id: "a", decision_type: "risk_approved", message: "ok" },
    { ...base, id: "b", decision_type: "risk_denied", message: "limit" },
  ];
  const groups = groupResearchDecisions(decisions);
  assert.equal(groups.length, 2);
});

test("keeps different candles separate", () => {
  const decisions = [
    { ...base, id: "a", candle_time: "2026-09-13T01:20:00+03:00" },
    { ...base, id: "b", candle_time: "2026-09-13T01:25:00+03:00" },
  ];
  const groups = groupResearchDecisions(decisions);
  assert.equal(groups.length, 2);
});
