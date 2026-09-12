"use client";

import { FinancialValue } from "@/components/trading/FinancialValue";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { cn } from "@/lib/utils";

type Props = {
  label: string;
  children: React.ReactNode;
  className?: string;
};

/** Home top summary — title then large value only (no header hints). */
export function HomeSummaryCard({ label, children, className }: Props) {
  return (
    <Card className={cn("flex h-full flex-col", className)}>
      <CardHeader className="mb-3">
        <CardTitle>{label}</CardTitle>
      </CardHeader>
      <CardContent>{children}</CardContent>
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
