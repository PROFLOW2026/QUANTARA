import { t } from "@/lib/i18n";

/** Display reward multiple as QUANTARA convention: 1:X (target profit / SL risk). */
export function formatRiskRewardLabel(ratio: number | null | undefined): string {
  if (ratio == null || !Number.isFinite(ratio) || ratio <= 0) {
    return t("home.risk_reward_unavailable");
  }
  const rounded =
    Math.abs(ratio - Math.round(ratio)) < 0.05 ? String(Math.round(ratio)) : ratio.toFixed(1);
  return `1:${rounded}`;
}

export function formatTargetProfitOrUnavailable(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) {
    return t("home.target_profit_undefined");
  }
  return new Intl.NumberFormat("he-IL", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(value);
}
