import { cn, formatCurrency, formatPercent } from "@/lib/utils";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

interface MetricCardProps {
  label: string;
  value: string | number;
  change?: number;
  changeLabel?: string;
  mono?: boolean;
  className?: string;
}

export function MetricCard({
  label,
  value,
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
            {changeLabel && <span className="text-muted mr-1"> {changeLabel}</span>}
          </p>
        )}
      </CardContent>
    </Card>
  );
}

export function MetricCardCurrency({
  label,
  value,
  className,
}: {
  label: string;
  value: number;
  className?: string;
}) {
  return (
    <MetricCard
      label={label}
      value={formatCurrency(value)}
      className={className}
    />
  );
}
