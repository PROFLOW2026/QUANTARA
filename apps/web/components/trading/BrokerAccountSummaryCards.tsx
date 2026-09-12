"use client";

import { useState } from "react";
import { FinancialValue } from "@/components/trading/FinancialValue";
import { Card, CardContent, CardHeader, CardTitle, HighlightCard } from "@/components/ui/card";
import type { BrokerAccountSummary } from "@/lib/api-client";
import { formatCurrencyOrUnavailable, formatPercentOrUnavailable } from "@/lib/display-text";
import { t } from "@/lib/i18n";

export function BrokerAccountSummaryCards({
  account,
  loading,
}: {
  account: BrokerAccountSummary | null | undefined;
  loading?: boolean;
}) {
  const [expanded, setExpanded] = useState(false);
  const v = (n: number | null | undefined) =>
    loading ? "…" : formatCurrencyOrUnavailable(n ?? null);
  const lev = (n: number | null | undefined) =>
    loading ? "…" : n != null ? `${n.toFixed(2)}×` : "—";
  const unavailable = t("home.broker_data_unavailable");

  const primaryCards = (
    <>
      <HighlightCard>
        <CardHeader>
          <CardTitle>{t("home.broker_equity")}</CardTitle>
        </CardHeader>
        <CardContent>
          <FinancialValue className="w-full">{v(account?.equity)}</FinancialValue>
          <p className="text-muted mt-1 text-xs">
            {t("home.broker_balance_cash", {
              balance: v(account?.balance),
              cash: v(account?.cash),
            })}
          </p>
        </CardContent>
      </HighlightCard>
      <HighlightCard>
        <CardHeader>
          <CardTitle>{t("home.broker_gross_exposure")}</CardTitle>
        </CardHeader>
        <CardContent>
          <FinancialValue className="w-full">{v(account?.gross_exposure)}</FinancialValue>
          <p className="text-muted mt-1 text-xs">
            {t("home.broker_net_exposure", { value: v(account?.net_exposure) })}
          </p>
        </CardContent>
      </HighlightCard>
      <HighlightCard>
        <CardHeader>
          <CardTitle>{t("home.broker_net_realized_pnl")}</CardTitle>
        </CardHeader>
        <CardContent>
          <FinancialValue className="w-full">
            {v(account?.net_realized_pnl ?? account?.realized_pnl)}
          </FinancialValue>
          {account?.gross_realized_pnl != null && account?.fees_paid != null && (
            <p className="text-muted mt-1 text-xs">
              {t("home.broker_gross_fees", {
                gross: v(account.gross_realized_pnl),
                fees: v(account.fees_paid),
              })}
            </p>
          )}
        </CardContent>
      </HighlightCard>
      <HighlightCard>
        <CardHeader>
          <CardTitle>{t("home.broker_available_margin")}</CardTitle>
        </CardHeader>
        <CardContent>
          <FinancialValue className="w-full">
            {v(account?.available_margin ?? account?.buying_power)}
          </FinancialValue>
          {account?.spot_crypto_cash != null && (
            <p className="text-muted mt-1 text-xs">
              {t("home.broker_spot_crypto_cash", { value: v(account.spot_crypto_cash) })}
            </p>
          )}
        </CardContent>
      </HighlightCard>
    </>
  );

  const secondaryCards = expanded ? (
    <div className="mt-4 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
      <Card>
        <CardHeader>
          <CardTitle>{t("home.broker_margin_used_free")}</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="font-mono text-lg">
            {v(account?.initial_margin_used)} / {v(account?.free_margin)}
          </p>
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle>{t("home.broker_leverage")}</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="font-mono text-lg">
            {lev(account?.gross_leverage)} / {lev(account?.net_leverage)}
          </p>
          {account?.margin_level_pct != null && (
            <p className="text-muted mt-1 text-sm">
              {t("home.broker_margin_level", {
                value: formatPercentOrUnavailable(account.margin_level_pct),
              })}
            </p>
          )}
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle>{t("home.broker_sl_risk")}</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="font-mono text-2xl">
            {loading
              ? "…"
              : account?.physical_risk_complete === false
                ? unavailable
                : formatCurrencyOrUnavailable(account?.physical_remaining_sl_risk_usd ?? null)}
          </p>
          <p className="text-muted mt-1 text-xs">
            {t("home.broker_projected_at_stops", {
              value: loading
                ? "…"
                : account?.physical_risk_complete === false
                  ? unavailable
                  : formatCurrencyOrUnavailable(
                      account?.projected_broker_equity_at_stops ?? account?.equity ?? null
                    ),
            })}
          </p>
        </CardContent>
      </Card>
    </div>
  ) : null;

  return (
    <div>
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">{primaryCards}</div>
      <button
        type="button"
        className="text-muted hover:text-foreground mt-3 text-sm underline-offset-2 hover:underline"
        onClick={() => setExpanded((open) => !open)}
      >
        {expanded ? t("home.broker_details_hide") : t("home.broker_details_more")}
      </button>
      {secondaryCards}
    </div>
  );
}
