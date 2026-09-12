import type { Decision } from "@/lib/api-client";
import { resolveDecisionType } from "@/lib/display-text";

/** Canonical grouping key for research decisions that differ only by portfolio/risk tier. */
export function researchDecisionGroupKey(decision: Decision): string {
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

export type DecisionGroup = {
  key: string;
  representative: Decision;
  members: Decision[];
};

export function groupResearchDecisions(decisions: Decision[]): DecisionGroup[] {
  const buckets = new Map<string, Decision[]>();
  for (const decision of decisions) {
    const key = researchDecisionGroupKey(decision);
    const list = buckets.get(key) ?? [];
    list.push(decision);
    buckets.set(key, list);
  }

  const groups: DecisionGroup[] = [];
  for (const [key, members] of Array.from(buckets.entries())) {
    const sortedMembers = [...members].sort((a, b) => {
      const risk = (a.risk_name_he ?? a.portfolio_name ?? "").localeCompare(
        b.risk_name_he ?? b.portfolio_name ?? ""
      );
      if (risk !== 0) return risk;
      return (a.portfolio_name ?? "").localeCompare(b.portfolio_name ?? "");
    });
    groups.push({
      key,
      representative: sortedMembers[0],
      members: sortedMembers,
    });
  }

  groups.sort((a, b) => {
    const tsA = new Date(a.representative.timestamp).getTime();
    const tsB = new Date(b.representative.timestamp).getTime();
    if (tsA !== tsB) return tsB - tsA;
    const robot = (a.representative.robot_label ?? "").localeCompare(
      b.representative.robot_label ?? ""
    );
    if (robot !== 0) return robot;
    return (a.representative.instrument ?? "").localeCompare(b.representative.instrument ?? "");
  });

  return groups;
}

export function isMultiTierDecisionGroup(group: DecisionGroup): boolean {
  return group.members.length > 1;
}

/** Flatten grouped rows for display — multi-tier groups become one row; singles stay one row. */
export function flattenDecisionGroups(groups: DecisionGroup[]): DecisionGroup[] {
  return groups;
}
