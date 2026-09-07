"use client";

import { useCallback, useEffect, useState } from "react";
import { PageHeader, ErrorBanner } from "@/components/layout/PageHeader";
import { CandlestickChart } from "@/components/charts/CandlestickChart";
import { PriceDisplay } from "@/components/trading/PriceDisplay";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow, EmptyState,
} from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";
import {
  api,
  ApiError,
  type Candle,
  type CandleLatest,
  type MarketProviderStatus,
} from "@/lib/api-client";
import { t } from "@/lib/i18n";
import { cn, formatDateTime, formatPrice } from "@/lib/utils";

const TIMEFRAMES = [
  { id: "5m", labelKey: "market.timeframe_5m" },
  { id: "15m", labelKey: "market.timeframe_15m" },
  { id: "1h", labelKey: "market.timeframe_1h" },
];

export default function MarketGoldPage() {
  const [timeframe, setTimeframe] = useState("1h");
  const [latest, setLatest] = useState<CandleLatest | null>(null);
  const [candles, setCandles] = useState<Candle[]>([]);
  const [status, setStatus] = useState<MarketProviderStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const fetchData = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [latestRes, candlesRes, statusRes] = await Promise.all([
        api.getCandlesLatest("XAU/USD"),
        api.getCandles({
          instrument: "XAU/USD",
          timeframe,
          limit: "20",
        }),
        api.getMarketStatus(),
      ]);
      setLatest(latestRes);
      setCandles(candlesRes);
      setStatus(statusRes);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : t("common.error"));
    } finally {
      setLoading(false);
    }
  }, [timeframe]);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  return (
    <>
      <PageHeader titleKey="market.gold_title" />
      {error && <div className="mb-4"><ErrorBanner message={error} /></div>}

      <div className="mb-4 grid gap-4 lg:grid-cols-3">
        <Card className="lg:col-span-1">
          <CardHeader><CardTitle>{t("market.current_price")}</CardTitle></CardHeader>
          <CardContent>
            {latest ? (
              <PriceDisplay
                value={latest.price}
                change={latest.change}
                changePct={latest.change_pct}
                size="xl"
              />
            ) : (
              <p className="text-muted">{loading ? t("common.loading") : t("common.no_data")}</p>
            )}
          </CardContent>
        </Card>

        <Card className="lg:col-span-2">
          <CardHeader>
            <CardTitle>{t("market.provider_status")}</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2 text-sm">
            <p>
              <Badge variant={status?.healthy ? "success" : "danger"}>
                {status?.healthy ? t("common.active") : t("common.inactive")}
              </Badge>
            </p>
            {status?.provider && (
              <p>
                {t("market.provider")}: <span className="font-mono">{status.provider}</span>
              </p>
            )}
            {status?.candle_counts && (
              <p className="font-mono text-xs text-muted">
                5m: {status.candle_counts["5m"] ?? 0} · 15m: {status.candle_counts["15m"] ?? 0} · 1h: {status.candle_counts["1h"] ?? 0}
              </p>
            )}
            {status?.last_fetch && (
              <p>{t("common.updated")}: {formatDateTime(status.last_fetch)}</p>
            )}
            {status?.stale && (
              <p className="text-warning">{t("market.stale_warning")}</p>
            )}
            {(status?.gaps ?? 0) > 0 && (
              <p className="text-warning">
                {t("market.gaps_warning")}: {status?.gaps}
              </p>
            )}
          </CardContent>
        </Card>
      </div>

      <Card className="mb-4">
        <CardHeader>
          <div className="flex items-center justify-between">
            <CardTitle>{t("market.chart")}</CardTitle>
            <div className="flex gap-1">
              {TIMEFRAMES.map((tf) => (
                <button
                  key={tf.id}
                  onClick={() => setTimeframe(tf.id)}
                  className={cn(
                    "rounded px-3 py-1 text-xs font-medium transition-colors",
                    timeframe === tf.id
                      ? "bg-accent text-white"
                      : "bg-surface-elevated text-muted hover:text-slate-200"
                  )}
                >
                  {t(tf.labelKey)}
                </button>
              ))}
            </div>
          </div>
        </CardHeader>
        <CardContent>
          <CandlestickChart candles={candles} />
        </CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle>{t("market.recent_candles")}</CardTitle></CardHeader>
        <CardContent>
          {!candles.length ? (
            <EmptyState message={loading ? t("common.loading") : t("common.no_data")} />
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>{t("common.time")}</TableHead>
                  <TableHead>{t("market.open")}</TableHead>
                  <TableHead>{t("market.high")}</TableHead>
                  <TableHead>{t("market.low")}</TableHead>
                  <TableHead>{t("market.close")}</TableHead>
                  <TableHead>{t("market.volume")}</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {candles.map((c, i) => (
                  <TableRow key={`${c.time}-${i}`}>
                    <TableCell>{formatDateTime(c.time)}</TableCell>
                    <TableCell className="font-mono">{formatPrice(c.open)}</TableCell>
                    <TableCell className="font-mono">{formatPrice(c.high)}</TableCell>
                    <TableCell className="font-mono">{formatPrice(c.low)}</TableCell>
                    <TableCell className="font-mono">{formatPrice(c.close)}</TableCell>
                    <TableCell className="font-mono">{c.volume ?? "—"}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>
    </>
  );
}
