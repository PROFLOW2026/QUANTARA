import { Badge } from "@/components/ui/badge";

type DecisionCategory = "approved" | "hold" | "denied" | "default";

function categorizeDecisionType(type: string): DecisionCategory {
  const upper = type.toUpperCase();
  if (
    upper.includes("BUY") ||
    upper.includes("SELL") ||
    upper.includes("EXECUTED") ||
    upper.includes("APPROVED")
  ) {
    return "approved";
  }
  if (
    upper.includes("HOLD") ||
    upper.includes("NO_SETUP") ||
    upper.includes("NO SIGNAL")
  ) {
    return "hold";
  }
  if (
    upper.includes("DENIED") ||
    upper.includes("HALT") ||
    upper.includes("RISK")
  ) {
    return "denied";
  }
  return "default";
}

const variantMap: Record<DecisionCategory, "success" | "warning" | "danger" | "muted"> = {
  approved: "success",
  hold: "warning",
  denied: "danger",
  default: "muted",
};

interface DecisionTypeBadgeProps {
  type: string;
}

export function DecisionTypeBadge({ type }: DecisionTypeBadgeProps) {
  const category = categorizeDecisionType(type);
  return <Badge variant={variantMap[category]}>{type}</Badge>;
}
