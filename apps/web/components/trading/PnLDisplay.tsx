import { cn, formatCurrency, formatPercent } from "@/lib/utils";

interface PnLDisplayProps {
  value: number;
  showPercent?: number;
  size?: "sm" | "md" | "lg";
  className?: string;
}

export function PnLDisplay({
  value,
  showPercent,
  size = "md",
  className,
}: PnLDisplayProps) {
  const positive = value >= 0;
  const sizeClasses = {
    sm: "text-sm",
    md: "text-base",
    lg: "text-xl",
  };

  return (
    <span
      className={cn(
        "font-mono font-medium",
        sizeClasses[size],
        positive ? "text-profit" : "text-loss",
        className
      )}
    >
      {positive ? "+" : ""}
      {formatCurrency(value)}
      {showPercent !== undefined && (
        <span className="mr-1 text-xs opacity-80">
          ({formatPercent(showPercent)})
        </span>
      )}
    </span>
  );
}
