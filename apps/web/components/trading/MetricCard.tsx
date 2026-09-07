import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { cn, formatCurrency, formatPercent } from "@/lib/utils";

interface MetricCardProps {
  label: string;
  value: string | number;
  hint?: string;
  change?: number;
  changeLabel?: string;
  mono?: boolean;
  className?: string;
}

export function MetricCard({
  label,
  value,
  hint,
  change,
  changeLabel,
  mono = true,
  className,
}: MetricCardProps) {
  const changePositive = change !== undefined && change >= 0;

  return (
    <Card className={className}>
      <CardHeader>
        <CardTitle>{label}</CardTitle>
        {hint ? <p className="text-xs text-muted">{hint}</p> : null}
      </CardHeader>
      <CardContent>
        <p className={cn("text-2xl font-semibold text-slate-100", mono && "font-mono")}>
          {value}
        </p>
        {change !== undefined && (
          <p
            className={cn(
              "mt-1 text-sm font-mono",
              changePositive ? "text-profit" : "text-loss"
            )}
          >
            {formatPercent(change)}
            {changeLabel && <span className="mr-1 text-muted"> {changeLabel}</span>}
          </p>
        )}
      </CardContent>
    </Card>
  );
}

export function MetricCardCurrency({
  label,
  value,
  hint,
  className,
}: {
  label: string;
  value: number;
  hint?: string;
  className?: string;
}) {
  return (
    <MetricCard
      label={label}
      hint={hint}
      value={formatCurrency(value)}
      className={className}
    />
  );
}
