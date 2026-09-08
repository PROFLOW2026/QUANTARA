import type { Decision } from "@/lib/api-client";

const INCIDENT_MESSAGE_MARKERS = [
  "MAX_DRAWDOWN",
  "HALT TRIGGERED",
  "PORTFOLIO HALTED",
  "PORTFOLIO HALT",
] as const;

/** Historical BTC mark / phantom drawdown artifacts — audit only, not current state. */
export function isHistoricalIncidentDecision(decision: Decision): boolean {
  const type = decision.decision_type.toLowerCase();
  if (type !== "risk_denied" && type !== "trading_halted") {
    return false;
  }
  const message = decision.message.toUpperCase();
  return INCIDENT_MESSAGE_MARKERS.some((marker) => message.includes(marker));
}
