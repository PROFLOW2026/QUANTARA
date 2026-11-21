"use client";

import { useId, useMemo, useState } from "react";
import {
  Area,
  CartesianGrid,
  ComposedChart,
  Line,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import {
  axisStyle,
  gridStyle,
  tooltipStyle,
  useChartTheme,
} from "@/components/charts/chart-theme";
import type { PayoffPoint, StrategyCandidate } from "@/lib/options-types";
import { toNum } from "@/lib/options-types";
import { cn, formatCurrency, formatPrice } from "@/lib/utils";

// ── Local pricing (UI estimate only; engine payoff_curve is authoritative) ───

const RISK_FREE_RATE = 0.04;
const FALLBACK_IV = 0.3;

function normCdf(x: number): number {
  const a1 = 0.254829592;
  const a2 = -0.284496736;
  const a3 = 1.421413741;
  const a4 = -1.453152027;
  const a5 = 1.061405429;
  const p = 0.3275911;
  const sign = x < 0 ? -1 : 1;
  const z = Math.abs(x) / Math.SQRT2;
  const t = 1 / (1 + p * z);
  const y = 1 - ((((a5 * t + a4) * t + a3) * t + a2) * t + a1) * t * Math.exp(-z * z);
  return 0.5 * (1 + sign * y);
}

function blackScholes(
  spot: number,
  strike: number,
  years: number,
  vol: number,
  right: "call" | "put"
): number {
  const intrinsic = right === "call" ? Math.max(spot - strike, 0) : Math.max(strike - spot, 0);
  if (years <= 0 || vol <= 0 || spot <= 0 || strike <= 0) return intrinsic;
  const sqrtT = Math.sqrt(years);
  const d1 = (Math.log(spot / strike) + (RISK_FREE_RATE + 0.5 * vol * vol) * years) / (vol * sqrtT);
  const d2 = d1 - vol * sqrtT;
  const disc = Math.exp(-RISK_FREE_RATE * years);
  return right === "call"
    ? spot * normCdf(d1) - strike * disc * normCdf(d2)
    : strike * disc * normCdf(-d2) - spot * normCdf(-d1);
}

export type CandidatePayoff = {
  points: PayoffPoint[];
  usedFallbackIv: boolean;
};

/** Estimate expiration and T+0 PnL curves (excl. fees) from a candidate's leg plan. */
export function computeCandidatePayoff(candidate: StrategyCandidate, steps = 120): CandidatePayoff {
  const spot = toNum(candidate.underlying_price) ?? 0;
  const strikes = candidate.legs.map((l) => toNum(l.strike)).filter((s): s is number => s != null);
  const lowRef = Math.min(spot, ...strikes);
  const highRef = Math.max(spot, ...strikes);
  const lo = Math.max(0.01, Math.min(spot * 0.8, lowRef * 0.92));
  const hi = Math.max(spot * 1.2, highRef * 1.08);
  const years = Math.max(candidate.dte, 0) / 365;
  let usedFallbackIv = false;

  const legs = candidate.legs.map((leg) => {
    const bid = toNum(leg.bid);
    const ask = toNum(leg.ask);
    const mid = toNum(leg.mid) ?? (bid != null && ask != null ? (bid + ask) / 2 : null);
    const iv = leg.implied_volatility ?? null;
    if (leg.right && iv == null) usedFallbackIv = true;
    return {
      sign: leg.side === "long" ? 1 : -1,
      units: leg.multiplier * leg.ratio * Math.max(candidate.contracts_qty, 1),
      right: leg.right,
      strike: toNum(leg.strike),
      entry: leg.right ? mid ?? 0 : mid ?? spot,
      iv: iv ?? FALLBACK_IV,
    };
  });

  const points: PayoffPoint[] = [];
  for (let i = 0; i <= steps; i++) {
    const price = lo + ((hi - lo) * i) / steps;
    let exp = 0;
    let t0 = 0;
    for (const leg of legs) {
      if (!leg.right || leg.strike == null) {
        const pnl = leg.sign * (price - leg.entry) * leg.units;
        exp += pnl;
        t0 += pnl;
        continue;
      }
      const intrinsic =
        leg.right === "call" ? Math.max(price - leg.strike, 0) : Math.max(leg.strike - price, 0);
      const theo = blackScholes(price, leg.strike, years, leg.iv, leg.right);
      exp += leg.sign * (intrinsic - leg.entry) * leg.units;
      t0 += leg.sign * (theo - leg.entry) * leg.units;
    }
    points.push({
      underlying_price: Math.round(price * 100) / 100,
      expiration_pnl: Math.round(exp * 100) / 100,
      t0_pnl: Math.round(t0 * 100) / 100,
    });
  }
  return { points, usedFallbackIv };
}

export function findBreakEvens(points: PayoffPoint[]): number[] {
  const out: number[] = [];
  for (let i = 1; i < points.length; i++) {
    const a = points[i - 1];
    const b = points[i];
    if (a.expiration_pnl === 0) {
      out.push(a.underlying_price);
      continue;
    }
    if ((a.expiration_pnl < 0 && b.expiration_pnl > 0) || (a.expiration_pnl > 0 && b.expiration_pnl < 0)) {
      const ratio = a.expiration_pnl / (a.expiration_pnl - b.expiration_pnl);
      out.push(a.underlying_price + ratio * (b.underlying_price - a.underlying_price));
    }
  }
  return out.map((v) => Math.round(v * 100) / 100);
}

// ── Chart ────────────────────────────────────────────────────────────────────

type Props = {
  points: PayoffPoint[];
  spot?: number | null;
  /** Engine-supplied break-evens; computed from the curve when omitted. */
  breakEvens?: number[];
  height?: number;
  note?: string;
  className?: string;
};

function compactUsd(v: number): string {
  return new Intl.NumberFormat("he-IL", {
    style: "currency",
    currency: "USD",
    notation: "compact",
    maximumFractionDigits: 1,
  }).format(v);
}

export function OptionsPayoffChart({ points, spot, breakEvens, height = 280, note, className }: Props) {
  const colors = useChartTheme();
  const axis = axisStyle(colors);
  const grid = gridStyle(colors);
  const tooltip = tooltipStyle(colors);
  const gradientId = `payoff-${useId().replace(/:/g, "")}`;
  const [showExpiration, setShowExpiration] = useState(true);
  const [showT0, setShowT0] = useState(true);

  const data = useMemo(
    () => [...points].sort((a, b) => a.underlying_price - b.underlying_price),
    [points]
  );

  const stats = useMemo(() => {
    if (!data.length) return null;
    const exp = data.map((p) => p.expiration_pnl);
    const allY = [...exp, ...data.map((p) => p.t0_pnl)];
    const maxExp = Math.max(...exp);
    const minExp = Math.min(...exp);
    const maxIdx = exp.indexOf(maxExp);
    const minIdx = exp.indexOf(minExp);
    const edge = (idx: number) => idx === 0 || idx === exp.length - 1;
    const yMax = Math.max(...allY);
    const yMin = Math.min(...allY);
    const offset = yMax <= 0 ? 0 : yMin >= 0 ? 1 : yMax / (yMax - yMin);
    const spotPoint =
      spot != null
        ? data.reduce((best, p) =>
            Math.abs(p.underlying_price - spot) < Math.abs(best.underlying_price - spot) ? p : best
          )
        : null;
    return {
      maxExp,
      minExp,
      profitUncapped: edge(maxIdx) && maxExp > 0,
      lossUncapped: edge(minIdx) && minExp < 0,
      offset,
      spotPoint,
    };
  }, [data, spot]);

  const bes = useMemo(
    () => (breakEvens && breakEvens.length ? breakEvens : findBreakEvens(data)),
    [breakEvens, data]
  );

  if (!data.length || !stats) {
    return (
      <div
        className={cn(
          "flex items-center justify-center rounded-lg border border-dashed border-border-nested bg-surface-inner",
          className
        )}
        style={{ height }}
      >
        <p className="text-sm text-muted">אין עקומת רווח/הפסד זמינה</p>
      </div>
    );
  }

  const toggleCls = (on: boolean) =>
    cn(
      "rounded px-2.5 py-1 text-xs transition-colors",
      on ? "bg-primary text-primary-foreground" : "border border-border text-muted hover:bg-surface-active"
    );

  return (
    <div className={cn("space-y-3", className)}>
      <div className="flex flex-wrap items-center gap-2">
        <button type="button" className={toggleCls(showExpiration)} onClick={() => setShowExpiration((v) => !v)}>
          רווח/הפסד בפקיעה
        </button>
        <button type="button" className={toggleCls(showT0)} onClick={() => setShowT0((v) => !v)}>
          T+0 (היום)
        </button>
        {note ? <span className="text-xs text-warning">{note}</span> : null}
      </div>

      <div dir="ltr" className="w-full">
        <ResponsiveContainer width="100%" height={height}>
          <ComposedChart data={data} margin={{ top: 16, right: 12, left: 4, bottom: 4 }}>
            <defs>
              <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
                <stop offset={0} stopColor={colors.profit} stopOpacity={0.35} />
                <stop offset={stats.offset} stopColor={colors.profit} stopOpacity={0.08} />
                <stop offset={stats.offset} stopColor={colors.loss} stopOpacity={0.08} />
                <stop offset={1} stopColor={colors.loss} stopOpacity={0.35} />
              </linearGradient>
            </defs>
            <CartesianGrid {...grid} />
            <XAxis
              type="number"
              dataKey="underlying_price"
              domain={["dataMin", "dataMax"]}
              tickFormatter={(v: number) => `$${formatPrice(v)}`}
              tick={axis.tick}
              axisLine={axis.axisLine}
              tickLine={axis.tickLine}
              minTickGap={28}
            />
            <YAxis
              tickFormatter={compactUsd}
              tick={axis.tick}
              axisLine={axis.axisLine}
              tickLine={axis.tickLine}
              width={64}
              domain={["auto", "auto"]}
            />
            <Tooltip
              contentStyle={tooltip.contentStyle}
              labelStyle={tooltip.labelStyle}
              itemStyle={tooltip.itemStyle}
              labelFormatter={(label) => `מחיר נכס: $${formatPrice(Number(label))}`}
              formatter={(value: number, name: string) => [
                formatCurrency(value),
                name === "expiration_pnl" ? "בפקיעה" : "T+0",
              ]}
            />
            <ReferenceLine y={0} stroke={colors.muted} strokeWidth={1.5} />
            {spot != null ? (
              <ReferenceLine
                x={spot}
                stroke={colors.accent}
                strokeDasharray="4 4"
                label={{ value: `Spot $${formatPrice(spot)}`, position: "top", fill: colors.accent, fontSize: 11 }}
              />
            ) : null}
            {bes.map((be) => (
              <ReferenceLine
                key={be}
                x={be}
                stroke={colors.muted}
                strokeDasharray="2 3"
                label={{ value: `BE ${formatPrice(be)}`, position: "insideBottom", fill: colors.muted, fontSize: 10 }}
              />
            ))}
            {showExpiration ? (
              <Area
                type="linear"
                dataKey="expiration_pnl"
                stroke={colors.profit}
                strokeWidth={2}
                fill={`url(#${gradientId})`}
                dot={false}
                activeDot={{ r: 4, stroke: colors.surface }}
                isAnimationActive={false}
              />
            ) : null}
            {showT0 ? (
              <Line
                type="monotone"
                dataKey="t0_pnl"
                stroke={colors.accent}
                strokeWidth={2}
                strokeDasharray="6 3"
                dot={false}
                activeDot={{ r: 4, stroke: colors.surface }}
                isAnimationActive={false}
              />
            ) : null}
          </ComposedChart>
        </ResponsiveContainer>
      </div>

      <div className="grid grid-cols-2 gap-2 text-xs sm:grid-cols-4">
        <div className="rounded-md border border-border-nested bg-surface-inner px-3 py-2">
          <p className="text-muted">רווח מקסימלי (בטווח)</p>
          <p className="font-mono font-medium text-profit">
            {stats.profitUncapped ? "לא מוגבל ↑" : formatCurrency(stats.maxExp)}
          </p>
        </div>
        <div className="rounded-md border border-border-nested bg-surface-inner px-3 py-2">
          <p className="text-muted">הפסד מקסימלי (בטווח)</p>
          <p className="font-mono font-medium text-loss">
            {stats.lossUncapped ? "לא מוגבל ↓" : formatCurrency(stats.minExp)}
          </p>
        </div>
        <div className="rounded-md border border-border-nested bg-surface-inner px-3 py-2">
          <p className="text-muted">נקודות איזון (BE)</p>
          <p className="font-mono font-medium text-foreground">
            {bes.length ? bes.map((b) => `$${formatPrice(b)}`).join(" · ") : "—"}
          </p>
        </div>
        <div className="rounded-md border border-border-nested bg-surface-inner px-3 py-2">
          <p className="text-muted">T+0 במחיר הנוכחי</p>
          <p
            className={cn(
              "font-mono font-medium",
              (stats.spotPoint?.t0_pnl ?? 0) >= 0 ? "text-profit" : "text-loss"
            )}
          >
            {stats.spotPoint ? formatCurrency(stats.spotPoint.t0_pnl) : "—"}
          </p>
        </div>
      </div>
    </div>
  );
}
