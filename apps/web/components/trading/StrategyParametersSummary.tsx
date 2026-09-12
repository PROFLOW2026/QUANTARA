import { formatStrategyParameterLines } from "@/lib/display-text";
import { t } from "@/lib/i18n";

interface StrategyParametersSummaryProps {
  parameters: Record<string, unknown> | null | undefined;
  compact?: boolean;
}

export function StrategyParametersSummary({
  parameters,
  compact = false,
}: StrategyParametersSummaryProps) {
  const lines = formatStrategyParameterLines(parameters);
  if (!lines.length) return <p className="text-sm text-muted">{t("common.no_data")}</p>;

  return (
    <dl className={compact ? "space-y-1 text-sm" : "grid gap-2 sm:grid-cols-2"}>
      {lines.map((line) => (
        <div key={line.label} className="flex items-start justify-between gap-3 rounded-md border border-border-nested bg-surface-inner px-3 py-2">
          <dt className="text-muted">{line.label}</dt>
          <dd className="font-mono text-foreground">{line.value}</dd>
        </div>
      ))}
    </dl>
  );
}
