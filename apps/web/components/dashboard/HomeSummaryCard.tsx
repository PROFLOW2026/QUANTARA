"use client";

import { FinancialValue } from "@/components/trading/FinancialValue";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { cn } from "@/lib/utils";

type Props = {
  label: string;
  hint?: string;
  children: React.ReactNode;
  className?: string;
};

/** Home top summary — title, value, optional hint. */
export function HomeSummaryCard({ label, hint, children, className }: Props) {
  return (
    <Card className={cn("flex h-full flex-col", className)}>
      <CardHeader className="mb-3">
        <CardTitle title={hint}>{label}</CardTitle>
      </CardHeader>
      <CardContent>
        {children}
        {hint ? <p className="mt-1 text-xs text-muted">{hint}</p> : null}
      </CardContent>
    </Card>
  );
}

export function HomeSummaryValue({
  children,
  className,
}: {
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <FinancialValue className={cn("w-full font-semibold", className)}>{children}</FinancialValue>
  );
}
