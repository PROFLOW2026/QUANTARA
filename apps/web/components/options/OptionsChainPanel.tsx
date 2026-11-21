"use client";

import { Fragment, useCallback, useEffect, useMemo, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { optionsApi } from "@/lib/options-api";
import type { DataFeedTier, OptionChainResponse, OptionQuoteSnapshot } from "@/lib/options-types";
import { OPTIONS_UNIVERSE, toNum } from "@/lib/options-types";
import { cn, formatDateTime, formatPrice } from "@/lib/utils";

// ── Shared feed badge ────────────────────────────────────────────────────────

type FeedKind = "opra" | "indicative" | "fixture" | "unavailable";

export function resolveFeedKind(tier: DataFeedTier | null | undefined, isIndicative = true): FeedKind {
  if (tier === "fixture") return "fixture";
  if (tier === "unavailable" || !tier) return "unavailable";
  if (tier === "alpaca_opra" && !isIndicative) return "opra";
  return "indicative";
}

const FEED_LABEL: Record<FeedKind, string> = {
  opra: "OPRA חי",
  indicative: "Indicative - נתוני השהיה/הערכה",
  fixture: "Fixture - סימולציה סגורה",
  unavailable: "אין נתוני שוק",
};

const FEED_VARIANT: Record<FeedKind, "success" | "warning" | "info" | "danger"> = {
  opra: "success",
  indicative: "warning",
  fixture: "info",
  unavailable: "danger",
};

export function FeedTierBadge({
  tier,
  isIndicative = true,
  isStale = false,
  isDelayedTrades = false,
  className,
}: {
  tier: DataFeedTier | null | undefined;
  isIndicative?: boolean;
  isStale?: boolean;
  isDelayedTrades?: boolean;
  className?: string;
}) {
  const kind = resolveFeedKind(tier, isIndicative);
  return (
    <span className={cn("inline-flex flex-wrap items-center gap-1", className)}>
      <Badge variant={FEED_VARIANT[kind]}>{FEED_LABEL[kind]}</Badge>
      {isDelayedTrades && kind !== "fixture" ? <Badge variant="outline">עסקאות בהשהיה</Badge> : null}
      {isStale ? <Badge variant="danger">נתונים לא עדכניים</Badge> : null}
    </span>
  );
}

// ── Formatting ───────────────────────────────────────────────────────────────

const intFmt = new Intl.NumberFormat("he-IL", { maximumFractionDigits: 0 });

function px(v: number | null): string {
  return v == null ? "—" : formatPrice(v);
}

function ivPct(iv: number | null | undefined): string {
  if (iv == null) return "—";
  const pct = iv > 3 ? iv : iv * 100;
  return `${pct.toFixed(1)}%`;
}

function spreadOf(q: OptionQuoteSnapshot): number | null {
  const bid = toNum(q.bid);
  const ask = toNum(q.ask);
  if (bid != null && ask != null && ask > 0) {
    const mid = (bid + ask) / 2;
    return mid > 0 ? ((ask - bid) / mid) * 100 : null;
  }
  return q.spread_pct ?? null;
}

function spreadTone(pct: number | null): string {
  if (pct == null) return "text-muted";
  if (pct > 10) return "text-loss";
  if (pct > 5) return "text-warning";
  return "text-muted";
}

type ChainRow = { strike: number; call?: OptionQuoteSnapshot; put?: OptionQuoteSnapshot };

// ── Panel ────────────────────────────────────────────────────────────────────

type Props = {
  initialChain?: OptionChainResponse | null;
  useFixtures?: boolean;
  defaultSymbol?: string;
  onSymbolChange?: (symbol: string) => void;
};

export function OptionsChainPanel({ initialChain, useFixtures = false, defaultSymbol, onSymbolChange }: Props) {
  const [symbol, setSymbol] = useState<string>(defaultSymbol ?? initialChain?.underlying_symbol ?? "SPY");
  const [chain, setChain] = useState<OptionChainResponse | null>(
    initialChain && initialChain.underlying_symbol === symbol ? initialChain : null
  );
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [minDte, setMinDte] = useState(3);
  const [maxDte, setMaxDte] = useState(60);
  const [expiration, setExpiration] = useState<string | null>(() => initialChain?.expirations?.[0] ?? null);
  const [compact, setCompact] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await optionsApi.getChain({ symbol, min_dte: minDte, max_dte: maxDte, use_fixtures: useFixtures });
      setChain(res);
    } catch (e) {
      setError(e instanceof Error ? e.message : "שגיאה בטעינת שרשרת");
    } finally {
      setLoading(false);
    }
  }, [symbol, minDte, maxDte, useFixtures]);

  useEffect(() => {
    void load();
  }, [load]);

  const expirations = useMemo(() => {
    if (!chain) return [];
    const fromContracts = chain.contracts.map((c) => c.contract.expiration_date);
    return Array.from(new Set([...(chain.expirations ?? []), ...fromContracts])).sort();
  }, [chain]);

  const effectiveExpiration = useMemo(() => {
    if (!expirations.length) return null;
    if (expiration && expirations.includes(expiration)) return expiration;
    return expirations[0];
  }, [expirations, expiration]);

  useEffect(() => {
    if (!expirations.length) {
      setExpiration(null);
    } else if (!expiration || !expirations.includes(expiration)) {
      setExpiration(expirations[0]);
    }
  }, [expirations, expiration]);

  const spot = useMemo(() => {
    if (!chain) return null;
    return toNum(chain.underlying_price) ?? toNum(chain.contracts[0]?.underlying_price);
  }, [chain]);

  const rows = useMemo<ChainRow[]>(() => {
    if (!chain || !effectiveExpiration) return [];
    const map = new Map<number, ChainRow>();
    for (const q of chain.contracts) {
      if (q.contract.expiration_date !== effectiveExpiration) continue;
      const strike = toNum(q.contract.strike);
      if (strike == null) continue;
      const row = map.get(strike) ?? { strike };
      if (q.contract.right === "call") row.call = q;
      else row.put = q;
      map.set(strike, row);
    }
    return Array.from(map.values()).sort((a, b) => a.strike - b.strike);
  }, [chain, effectiveExpiration]);

  const spotInsertIdx = useMemo(() => {
    if (spot == null || !rows.length) return -1;
    return rows.findIndex((r) => r.strike >= spot);
  }, [rows, spot]);

  const multipliers = useMemo(() => {
    const set = new Set<number>();
    rows.forEach((r) => {
      if (r.call) set.add(r.call.contract.multiplier);
      if (r.put) set.add(r.put.contract.multiplier);
    });
    return Array.from(set);
  }, [rows]);

  const dteOf = (iso: string) => {
    const ms = new Date(`${iso}T21:00:00Z`).getTime() - Date.now();
    return Math.max(0, Math.ceil(ms / 86_400_000));
  };

  const changeSymbol = (s: string) => {
    setSymbol(s);
    setChain(null);
    onSymbolChange?.(s);
  };

  const sideHeaders = compact ? ["Δ", "Bid", "Ask"] : ["Vol", "OI", "Θ", "Δ", "IV", "Spread%", "Bid", "Ask"];
  const putHeaders = [...sideHeaders].reverse();
  const colSpan = sideHeaders.length * 2 + 1;

  const renderSide = (q: OptionQuoteSnapshot | undefined, side: "call" | "put", itm: boolean) => {
    const bg = itm ? "bg-surface-inner" : "";
    if (!q) {
      return sideHeaders.map((h) => (
        <td key={`${side}-${h}`} className={cn("px-2 py-1.5 text-center text-muted", bg)}>
          —
        </td>
      ));
    }
    const spread = spreadOf(q);
    const cells: Record<string, React.ReactNode> = {
      Vol: intFmt.format(q.volume),
      OI: intFmt.format(q.open_interest || q.contract.open_interest),
      "Θ": q.greeks.theta != null ? q.greeks.theta.toFixed(3) : "—",
      "Δ": q.greeks.delta != null ? q.greeks.delta.toFixed(2) : "—",
      IV: ivPct(q.implied_volatility),
      "Spread%": <span className={spreadTone(spread)}>{spread != null ? `${spread.toFixed(1)}%` : "—"}</span>,
      Bid: <span className="text-profit">{px(toNum(q.bid))}</span>,
      Ask: <span className="text-loss">{px(toNum(q.ask))}</span>,
    };
    const order = side === "call" ? sideHeaders : putHeaders;
    return order.map((h) => (
      <td
        key={`${side}-${h}`}
        className={cn("px-2 py-1.5 text-center font-mono tabular-nums", bg, q.is_stale && "opacity-60")}
        title={q.data_warnings.length ? q.data_warnings.join(" · ") : q.contract.occ_symbol}
      >
        {cells[h]}
      </td>
    ));
  };

  return (
    <Card>
      <CardHeader className="flex-wrap gap-2">
        <CardTitle>שרשרת אופציות</CardTitle>
        {chain ? (
          <FeedTierBadge
            tier={chain.feed_tier}
            isIndicative={chain.is_indicative}
            isStale={chain.is_stale}
            isDelayedTrades={chain.is_delayed_trades}
          />
        ) : null}
      </CardHeader>
      <CardContent className="space-y-3">
        {/* Symbols */}
        <div className="flex flex-wrap gap-1.5">
          {OPTIONS_UNIVERSE.map((s) => (
            <button
              key={s}
              type="button"
              onClick={() => changeSymbol(s)}
              className={cn(
                "rounded px-3 py-1 font-mono text-sm transition-colors",
                symbol === s
                  ? "bg-primary text-primary-foreground"
                  : "border border-border text-muted hover:bg-surface-active"
              )}
            >
              {s}
            </button>
          ))}
        </div>

        {/* Filters */}
        <div className="flex flex-wrap items-end gap-3 text-sm">
          <label className="flex flex-col gap-1">
            <span className="text-xs text-muted">פקיעה</span>
            <select
              value={effectiveExpiration ?? ""}
              onChange={(e) => setExpiration(e.target.value || null)}
              className="rounded-md border border-input-border bg-surface-inner px-2 py-1.5 text-sm text-foreground"
              disabled={!expirations.length}
            >
              {expirations.map((exp) => (
                <option key={exp} value={exp}>
                  {exp} · {dteOf(exp)} ימים
                </option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-xs text-muted">DTE מינ׳</span>
            <input
              type="number"
              min={0}
              value={minDte}
              onChange={(e) => setMinDte(Math.max(0, Number(e.target.value) || 0))}
              className="w-20 rounded-md border border-input-border bg-surface-inner px-2 py-1.5 text-sm text-foreground"
            />
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-xs text-muted">DTE מקס׳</span>
            <input
              type="number"
              min={1}
              value={maxDte}
              onChange={(e) => setMaxDte(Math.max(1, Number(e.target.value) || 1))}
              className="w-20 rounded-md border border-input-border bg-surface-inner px-2 py-1.5 text-sm text-foreground"
            />
          </label>
          <button
            type="button"
            onClick={() => setCompact((v) => !v)}
            className="rounded border border-border px-3 py-1.5 text-xs text-muted hover:bg-surface-active"
          >
            {compact ? "תצוגה מלאה" : "תצוגה מקוצרת"}
          </button>
          <button
            type="button"
            onClick={() => void load()}
            disabled={loading}
            className="rounded border border-border px-3 py-1.5 text-xs text-muted hover:bg-surface-active disabled:opacity-50"
          >
            {loading ? "טוען…" : "רענון"}
          </button>
        </div>

        {/* Meta */}
        {chain ? (
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted">
            <span>
              נכס בסיס: <span className="font-mono text-foreground">{chain.underlying_symbol}</span>
              {spot != null ? <span className="ms-1 font-mono text-foreground">${formatPrice(spot)}</span> : null}
            </span>
            <span>שוק: {chain.market_open ? "פתוח" : "סגור"}</span>
            <span>נכון ל־{formatDateTime(chain.as_of)}</span>
            <span>ספק: {chain.provider_name}</span>
            <span>מכפיל חוזה: {multipliers.length ? multipliers.join(" / ") : "100"}</span>
          </div>
        ) : null}

        {chain?.warnings?.length ? (
          <div className="rounded-md border border-warning-border bg-warning-bg px-3 py-2 text-xs text-warning">
            {chain.warnings.map((w) => (
              <p key={w}>⚠ {w}</p>
            ))}
          </div>
        ) : null}

        {error ? (
          <div className="rounded-md border border-loss-border bg-loss-bg px-3 py-2 text-sm text-loss">{error}</div>
        ) : null}

        {/* Chain table — fixed LTR so CALLS stay left, PUTS right */}
        {loading && !chain ? (
          <p className="py-6 text-center text-sm text-muted">טוען שרשרת…</p>
        ) : rows.length === 0 ? (
          <p className="py-6 text-center text-sm text-muted">אין חוזים זמינים לפקיעה/טווח שנבחר.</p>
        ) : (
          <div dir="ltr" className="w-full overflow-x-auto rounded-md border border-table-border scrollbar-thin">
            <table className="w-full min-w-max text-xs">
              <thead className="bg-table-header text-table-header-text">
                <tr>
                  <th colSpan={sideHeaders.length} className="border-b border-table-border py-1.5 text-center text-profit">
                    CALLS · קול
                  </th>
                  <th className="border-x border-b border-table-border px-2 py-1.5 text-center">Strike</th>
                  <th colSpan={sideHeaders.length} className="border-b border-table-border py-1.5 text-center text-loss">
                    PUTS · פוט
                  </th>
                </tr>
                <tr>
                  {sideHeaders.map((h) => (
                    <th key={`ch-${h}`} className="px-2 py-1.5 text-center font-medium">
                      {h}
                    </th>
                  ))}
                  <th className="border-x border-table-border px-2 py-1.5 text-center font-medium">$</th>
                  {putHeaders.map((h) => (
                    <th key={`ph-${h}`} className="px-2 py-1.5 text-center font-medium">
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rows.map((row, idx) => {
                  const callItm = spot != null && row.strike < spot;
                  const putItm = spot != null && row.strike > spot;
                  return (
                    <Fragment key={row.strike}>
                      {idx === spotInsertIdx && spot != null ? (
                        <tr>
                          <td colSpan={colSpan} className="bg-primary-soft py-0.5 text-center text-[11px] font-medium text-accent">
                            ▲ Spot ${formatPrice(spot)} ▼
                          </td>
                        </tr>
                      ) : null}
                      <tr className="border-b border-table-border bg-table-row hover:bg-table-hover">
                        {renderSide(row.call, "call", callItm)}
                        <td className="border-x border-table-border bg-surface-highlight px-3 py-1.5 text-center font-mono font-semibold text-foreground">
                          {formatPrice(row.strike)}
                        </td>
                        {renderSide(row.put, "put", putItm)}
                      </tr>
                    </Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
        <p className="text-xs text-muted">
          רקע מודגש = In-The-Money. Θ ליום קלנדרי לכל מניה; Δ לכל חוזה. ציטוטים אינם ביצוע בבורסה — סימולציה פרטית בלבד.
        </p>
      </CardContent>
    </Card>
  );
}
