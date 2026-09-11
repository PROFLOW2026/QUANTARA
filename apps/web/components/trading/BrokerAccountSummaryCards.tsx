"use client";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { BrokerAccountSummary } from "@/lib/api-client";
import { formatCurrencyOrUnavailable, formatPercentOrUnavailable } from "@/lib/display-text";

export function BrokerAccountSummaryCards({
  account,
  loading,
}: {
  account: BrokerAccountSummary | null | undefined;
  loading?: boolean;
}) {
  const v = (n: number | null | undefined) =>
    loading ? "…" : formatCurrencyOrUnavailable(n ?? null);
  const lev = (n: number | null | undefined) =>
    loading ? "…" : n != null ? `${n.toFixed(2)}×` : "—";

  return (
    <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
      <Card>
        <CardHeader>
          <CardTitle>Broker Equity</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="font-mono text-2xl">{v(account?.equity)}</p>
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle>Available Margin</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="font-mono text-2xl">{v(account?.available_margin ?? account?.buying_power)}</p>
          {account?.spot_crypto_cash != null && (
            <p className="text-muted mt-1 text-xs">Spot crypto cash: {v(account.spot_crypto_cash)}</p>
          )}
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle>Margin Used / Free</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="font-mono text-lg">
            {v(account?.initial_margin_used)} / {v(account?.free_margin)}
          </p>
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle>Gross / Net Leverage</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="font-mono text-lg">
            {lev(account?.gross_leverage)} / {lev(account?.net_leverage)}
          </p>
          {account?.margin_level_pct != null && (
            <p className="text-muted mt-1 text-sm">
              Margin level {formatPercentOrUnavailable(account.margin_level_pct)}
            </p>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
