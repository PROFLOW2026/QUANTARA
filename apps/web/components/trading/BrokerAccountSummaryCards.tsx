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
    <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4 xl:grid-cols-6">
      <Card>
        <CardHeader>
          <CardTitle>Broker Equity</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="font-mono text-2xl">{v(account?.equity)}</p>
          <p className="text-muted mt-1 text-xs">Balance {v(account?.balance)} · Cash {v(account?.cash)}</p>
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle>Physical Gross Exposure</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="font-mono text-2xl">{v(account?.gross_exposure)}</p>
          <p className="text-muted mt-1 text-xs">Net exposure {v(account?.net_exposure)}</p>
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle>Net Realized P&L</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="font-mono text-2xl">{v(account?.net_realized_pnl ?? account?.realized_pnl)}</p>
          {account?.gross_realized_pnl != null && account?.fees_paid != null && (
            <p className="text-muted mt-1 text-xs">
              Gross {v(account.gross_realized_pnl)} · Fees {v(account.fees_paid)}
            </p>
          )}
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
      <Card>
        <CardHeader>
          <CardTitle>Physical Remaining SL Risk</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="font-mono text-2xl">
            {loading
              ? "…"
              : account?.physical_risk_complete === false
                ? "לא זמין / נתונים חסרים"
                : formatCurrencyOrUnavailable(account?.physical_remaining_sl_risk_usd ?? null)}
          </p>
          <p className="text-muted mt-1 text-xs">
            Projected broker equity at stops{" "}
            {loading
              ? "…"
              : account?.physical_risk_complete === false
                ? "לא זמין / נתונים חסרים"
                : formatCurrencyOrUnavailable(
                    account?.projected_broker_equity_at_stops ?? account?.equity ?? null
                  )}
          </p>
        </CardContent>
      </Card>
    </div>
  );
}
