import { cn, formatCurrency, formatPercent } from "@/lib/utils";

interface PriceDisplayProps {
  value: number;
  change?: number;
  changePct?: number;
  size?: "sm" | "md" | "lg" | "xl";
  className?: string;
}

export function PriceDisplay({
  value,
  change,
  changePct,
  size = "md",
  className,
}: PriceDisplayProps) {
  const positive = (change ?? 0) >= 0;
  const sizeClasses = {
    sm: "text-lg",
    md: "text-2xl",
    lg: "text-3xl",
    xl: "text-4xl",
  };

  return (
    <div className={className}>
      <span className={cn("font-mono font-bold text-slate-100", sizeClasses[size])}>
        {formatCurrency(value).replace("$", "$")}
      </span>
      {(change !== undefined || changePct !== undefined) && (
        <div
          className={cn(
            "mt-1 font-mono text-sm",
            positive ? "text-profit" : "text-loss"
          )}
        >
          {change !== undefined && (
            <span>{positive ? "+" : ""}{change.toFixed(2)} </span>
          )}
          {changePct !== undefined && <span>({formatPercent(changePct)})</span>}
        </div>
      )}
    </div>
  );
}
