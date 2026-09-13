"use client";

import { FinancialValue } from "@/components/trading/FinancialValue";
import { usePriceChangeFlash } from "@/hooks/usePriceChangeFlash";
import { priceFlashClassName } from "@/lib/price-flash";
import { formatCurrency } from "@/lib/utils";
import { cn } from "@/lib/utils";

export function AssetLivePrice({
  dbSymbol,
  price,
  className,
  variant = "compact",
}: {
  dbSymbol: string;
  price: number | null | undefined;
  className?: string;
  variant?: "summary" | "compact";
}) {
  const flash = usePriceChangeFlash(price, dbSymbol);

  return (
    <FinancialValue
      variant={variant}
      className={cn(priceFlashClassName(flash), className)}
    >
      {price != null ? formatCurrency(price) : "—"}
    </FinancialValue>
  );
}
