"use client";

import { cn } from "@/lib/utils";

type FinancialValueProps = {
  children: React.ReactNode;
  className?: string;
  /** summary = dashboard metric cards; compact = asset card cells */
  variant?: "summary" | "compact";
};

/** Responsive financial typography — tabular, nowrap, scales down for long values. */
export function FinancialValue({
  children,
  className,
  variant = "summary",
}: FinancialValueProps) {
  return (
    <p
      className={cn(
        "max-w-full font-mono tabular-nums whitespace-nowrap leading-tight text-financial",
        variant === "summary" &&
          "text-[clamp(0.8125rem,0.85vw+0.45rem,1.375rem)]",
        variant === "compact" &&
          "text-[clamp(0.6875rem,1vw+0.35rem,0.8125rem)]",
        className
      )}
    >
      {children}
    </p>
  );
}
