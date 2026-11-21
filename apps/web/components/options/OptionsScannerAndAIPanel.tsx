"use client";

import { Fragment, useMemo, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/table";
import { FeedTierBadge } from "@/components/options/OptionsChainPanel";
import { OptionsPayoffChart, computeCandidatePayoff } from "@/components/options/OptionsPayoffChart";
import type { AIAction, AIDecisionRecord, StrategyCandidate, StrategyType } from "@/lib/options-types";
import { STRATEGY_TYPES, strategyLabel, toNum } from "@/lib/options-types";
import { cn, formatCurrency, formatDateTime, formatPrice } from "@/lib/utils";

type SortKey = "rules_score" | "pop" | "premium" | "dte" | "max_loss";

const SORT_LABELS: Record<SortKey, string> = {
  rules_score: "ציון חוקים",
  pop: "PoP%",
  premium: "פרמיה נטו",
  dte: "DTE",
  max_loss: "הפסד מקס׳ (נמוך)",
};

const ACTION_VARIANT: Record<AIAction, "success" | "muted" | "danger" | "warning" | "outline"> = {
  OPEN: "success",
  HOLD: "muted",
  CLOSE: "danger",
  ROLL: "warning",
  SKIP: "outline",
};

const ACTION_HE: Record<AIAction, string> = {
  OPEN: "פתיחה",
  HOLD: "החזקה",
  CLOSE: "סגירה",
  ROLL: "גלגול",
  SKIP: "דילוג",
};

function decidedByLabel(d: AIDecisionRecord): { label: string; variant: "info" | "muted" | "warning" } {
  if (d.decided_by === "groq_openai_gpt_oss_120b") {
    return { label: `Groq · ${d.model_name ?? "openai/gpt-oss-120b"}`, variant: "info" };
  }
  if (d.decided_by === "skills_rules_fallback") {
    return { label: "גיבוי חוקים (AI לא זמין)", variant: "warning" };
  }
  return { label: "מנוע חוקים", variant: "muted" };
}

function pct(v: number | null | undefined, digits = 1): string {
  if (v == null) return "—";
  const p = Math.abs(v) <= 1 ? v * 100 : v;
  return `${p.toFixed(digits)}%`;
}

function usd(v: number | null): string {
  return v == null ? "—" : formatCurrency(v);
}

function SelectFilter({
  label,
  value,
  onChange,
  options,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  options: { value: string; label: string }[];
}) {
  return (
    <label className="flex flex-col gap-1">
      <span className="text-xs text-muted">{label}</span>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="rounded-md border border-input-border bg-surface-inner px-2 py-1.5 text-sm text-foreground"
      >
        {options.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
    </label>
  );
}

// ── Candidate detail ─────────────────────────────────────────────────────────

function CandidateDetail({ c }: { c: StrategyCandidate }) {
  const payoff = useMemo(() => computeCandidatePayoff(c), [c]);
  const spot = toNum(c.underlying_price);
  const bes = c.break_even_prices.map((b) => toNum(b)).filter((b): b is number => b != null);
  const evidence = Object.entries(c.skills_evidence ?? {}).slice(0, 8);

  return (
    <div className="space-y-3 rounded-md border border-border-nested bg-surface-inner p-3">
      <div className="grid gap-3 lg:grid-cols-[1fr_1.4fr]">
        <div className="space-y-2">
          <p className="text-xs font-medium text-foreground">רגליים</p>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead className="text-muted">
                <tr>
                  <th className="py-1 text-start font-medium">צד</th>
                  <th className="py-1 text-start font-medium">סוג</th>
                  <th className="py-1 text-start font-medium">Strike</th>
                  <th className="py-1 text-start font-medium">Bid/Ask</th>
                  <th className="py-1 text-start font-medium">Δ</th>
                  <th className="py-1 text-start font-medium">IV</th>
                </tr>
              </thead>
              <tbody>
                {c.legs.map((leg, i) => (
                  <tr key={leg.occ_symbol ?? `${leg.leg_role}-${i}`} className="border-t border-border/40">
                    <td className={cn("py-1", leg.side === "long" ? "text-profit" : "text-loss")}>
                      {leg.side === "long" ? "קנייה" : "מכירה"} ×{leg.ratio * c.contracts_qty}
                    </td>
                    <td className="py-1">{leg.right ? leg.right.toUpperCase() : "מניה"}</td>
                    <td className="py-1 font-mono">{leg.strike != null ? formatPrice(toNum(leg.strike) ?? 0) : "—"}</td>
                    <td className="py-1 font-mono">
                      {toNum(leg.bid) != null ? formatPrice(toNum(leg.bid)!) : "—"} /{" "}
                      {toNum(leg.ask) != null ? formatPrice(toNum(leg.ask)!) : "—"}
                    </td>
                    <td className="py-1 font-mono">{leg.greeks.delta != null ? leg.greeks.delta.toFixed(2) : "—"}</td>
                    <td className="py-1 font-mono">{pct(leg.implied_volatility)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="grid grid-cols-3 gap-2 text-xs">
            <div>
              <p className="text-muted">Δ נטו</p>
              <p className="font-mono">{c.net_delta.toFixed(2)}</p>
            </div>
            <div>
              <p className="text-muted">Θ נטו</p>
              <p className={cn("font-mono", c.net_theta >= 0 ? "text-profit" : "text-loss")}>{c.net_theta.toFixed(2)}</p>
            </div>
            <div>
              <p className="text-muted">Vega נטו</p>
              <p className="font-mono">{c.net_vega.toFixed(2)}</p>
            </div>
            <div>
              <p className="text-muted">נזילות</p>
              <p className="font-mono">{c.liquidity_score.toFixed(2)}</p>
            </div>
            <div>
              <p className="text-muted">ציון חוקים</p>
              <p className="font-mono">{c.rules_score.toFixed(2)}</p>
            </div>
            <div>
              <p className="text-muted">פרמיה ליחידה</p>
              <p className="font-mono">{formatPrice(toNum(c.estimated_net_premium_per_unit) ?? 0)}</p>
            </div>
          </div>
          {c.rationale_summary ? (
            <p className="text-xs leading-relaxed text-foreground-secondary">{c.rationale_summary}</p>
          ) : null}
          {c.warnings.length ? (
            <ul className="space-y-0.5 text-xs text-warning">
              {c.warnings.map((w) => (
                <li key={w}>⚠ {w}</li>
              ))}
            </ul>
          ) : null}
          {evidence.length ? (
            <div className="flex flex-wrap gap-1">
              {evidence.map(([k, v]) => (
                <Badge key={k} variant="outline" className="font-mono">
                  {k}: {typeof v === "object" ? "…" : String(v)}
                </Badge>
              ))}
            </div>
          ) : null}
        </div>
        <OptionsPayoffChart
          points={payoff.points}
          spot={spot}
          breakEvens={bes.length ? bes : undefined}
          height={240}
          note={
            payoff.usedFallbackIv
              ? "הערכה מקומית (IV ברירת מחדל 30%) · לא כולל עמלות"
              : "הערכה מקומית לפי Black-Scholes · לא כולל עמלות"
          }
        />
      </div>
    </div>
  );
}

// ── Panel ────────────────────────────────────────────────────────────────────

type Props = {
  opportunities: StrategyCandidate[];
  decisions: AIDecisionRecord[];
  accountSlug: string | null;
  groqModel?: string;
  groqAvailable?: boolean;
  onSimulate?: (candidate: StrategyCandidate) => Promise<void> | void;
  simulatingId?: string | null;
};

export function OptionsScannerAndAIPanel({
  opportunities,
  decisions,
  accountSlug,
  groqModel = "openai/gpt-oss-120b",
  groqAvailable,
  onSimulate,
  simulatingId,
}: Props) {
  const [strategy, setStrategy] = useState<string>("all");
  const [symbol, setSymbol] = useState<string>("all");
  const [flow, setFlow] = useState<"all" | "credit" | "debit">("all");
  const [sort, setSort] = useState<SortKey>("rules_score");
  const [expanded, setExpanded] = useState<string | null>(null);
  const [guardFilter, setGuardFilter] = useState<"all" | "passed" | "rejected">("all");

  const symbols = useMemo(
    () => Array.from(new Set(opportunities.map((o) => o.underlying_symbol))).sort(),
    [opportunities]
  );

  const strategyCounts = useMemo(() => {
    const m = new Map<string, number>();
    opportunities.forEach((o) => m.set(o.strategy_type, (m.get(o.strategy_type) ?? 0) + 1));
    return m;
  }, [opportunities]);

  const filtered = useMemo(() => {
    const list = opportunities.filter(
      (o) =>
        (strategy === "all" || o.strategy_type === strategy) &&
        (symbol === "all" || o.underlying_symbol === symbol) &&
        (flow === "all" || (flow === "credit" ? o.is_credit : !o.is_credit))
    );
    const score = (o: StrategyCandidate): number => {
      switch (sort) {
        case "pop":
          return o.probability_of_profit ?? -1;
        case "premium":
          return toNum(o.estimated_net_premium_total) ?? 0;
        case "dte":
          return -o.dte;
        case "max_loss":
          return -(toNum(o.max_loss_usd) ?? Infinity);
        default:
          return o.rules_score;
      }
    };
    return [...list].sort((a, b) => score(b) - score(a));
  }, [opportunities, strategy, symbol, flow, sort]);

  const filteredDecisions = useMemo(
    () =>
      [...decisions]
        .filter((d) =>
          guardFilter === "all" ? true : guardFilter === "passed" ? d.risk_guard_passed : !d.risk_guard_passed
        )
        .sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime()),
    [decisions, guardFilter]
  );

  const passedCount = decisions.filter((d) => d.risk_guard_passed).length;

  const simulateBtn = (c: StrategyCandidate) => (
    <button
      type="button"
      disabled={!onSimulate || !accountSlug || simulatingId === c.candidate_id}
      onClick={() => void onSimulate?.(c)}
      className="rounded bg-primary px-2.5 py-1 text-xs text-primary-foreground hover:bg-primary-hover disabled:opacity-50"
      title={!accountSlug ? "בחר חשבון כדי לסמלץ" : "פתיחה מדומה בחשבון הנבחר"}
    >
      {simulatingId === c.candidate_id ? "מסמלץ…" : "סימולציה"}
    </button>
  );

  const inspectBtn = (c: StrategyCandidate) => (
    <button
      type="button"
      onClick={() => setExpanded((cur) => (cur === c.candidate_id ? null : c.candidate_id))}
      className="rounded border border-border px-2.5 py-1 text-xs text-muted hover:bg-surface-active"
      aria-expanded={expanded === c.candidate_id}
    >
      {expanded === c.candidate_id ? "הסתר" : "Payoff"}
    </button>
  );

  return (
    <div className="space-y-4">
      {/* Scanner */}
      <Card>
        <CardHeader className="flex-wrap gap-2">
          <CardTitle>סורק פרמיות ואסטרטגיות</CardTitle>
          <span className="text-xs text-muted">
            {filtered.length} מתוך {opportunities.length} הזדמנויות
          </span>
        </CardHeader>
        <CardContent className="space-y-3">
          {/* Strategy chips */}
          <div className="flex flex-wrap gap-1.5">
            <button
              type="button"
              onClick={() => setStrategy("all")}
              className={cn(
                "rounded px-2.5 py-1 text-xs transition-colors",
                strategy === "all"
                  ? "bg-primary text-primary-foreground"
                  : "border border-border text-muted hover:bg-surface-active"
              )}
            >
              הכל
            </button>
            {STRATEGY_TYPES.map((s: StrategyType) => (
              <button
                key={s}
                type="button"
                onClick={() => setStrategy(s)}
                className={cn(
                  "rounded px-2.5 py-1 text-xs transition-colors",
                  strategy === s
                    ? "bg-primary text-primary-foreground"
                    : "border border-border text-muted hover:bg-surface-active"
                )}
              >
                {strategyLabel(s)}
                <span className="ms-1 opacity-70">({strategyCounts.get(s) ?? 0})</span>
              </button>
            ))}
          </div>

          <div className="flex flex-wrap items-end gap-3">
            <SelectFilter
              label="נכס"
              value={symbol}
              onChange={setSymbol}
              options={[{ value: "all", label: "כל הנכסים" }, ...symbols.map((s) => ({ value: s, label: s }))]}
            />
            <SelectFilter
              label="זרימת מזומן"
              value={flow}
              onChange={(v) => setFlow(v as typeof flow)}
              options={[
                { value: "all", label: "Credit + Debit" },
                { value: "credit", label: "Credit בלבד (גביית פרמיה)" },
                { value: "debit", label: "Debit בלבד (תשלום פרמיה)" },
              ]}
            />
            <SelectFilter
              label="מיון"
              value={sort}
              onChange={(v) => setSort(v as SortKey)}
              options={(Object.keys(SORT_LABELS) as SortKey[]).map((k) => ({ value: k, label: SORT_LABELS[k] }))}
            />
          </div>

          {filtered.length === 0 ? (
            <EmptyState message="אין הזדמנויות תואמות. הרץ מחזור סריקה או שנה את הסינון." />
          ) : (
            <>
              {/* Desktop table */}
              <div className="hidden overflow-x-auto rounded-md border border-table-border md:block">
                <table className="w-full text-sm">
                  <thead className="bg-table-header text-xs text-table-header-text">
                    <tr>
                      <th className="px-3 py-2 text-start font-medium">אסטרטגיה</th>
                      <th className="px-3 py-2 text-start font-medium">נכס</th>
                      <th className="px-3 py-2 text-start font-medium">DTE</th>
                      <th className="px-3 py-2 text-start font-medium">פרמיה נטו</th>
                      <th className="px-3 py-2 text-start font-medium">רווח מקס׳</th>
                      <th className="px-3 py-2 text-start font-medium">הפסד מקס׳</th>
                      <th className="px-3 py-2 text-start font-medium">בטוחות</th>
                      <th className="px-3 py-2 text-start font-medium">PoP</th>
                      <th className="px-3 py-2 text-start font-medium">IV Rank</th>
                      <th className="px-3 py-2 text-start font-medium">נתונים</th>
                      <th className="px-3 py-2 text-start font-medium" />
                    </tr>
                  </thead>
                  <tbody>
                    {filtered.map((c) => {
                      const premium = toNum(c.estimated_net_premium_total) ?? 0;
                      const maxProfit = toNum(c.max_profit_usd);
                      return (
                        <Fragment key={c.candidate_id}>
                          <tr className="border-b border-table-border bg-table-row hover:bg-table-hover">
                            <td className="px-3 py-2">
                              <span className="font-medium">{strategyLabel(c.strategy_type)}</span>
                              <Badge variant={c.is_credit ? "success" : "outline"} className="ms-2">
                                {c.is_credit ? "Credit" : "Debit"}
                              </Badge>
                            </td>
                            <td className="px-3 py-2 font-mono">
                              {c.underlying_symbol}
                              <span className="ms-1 text-xs text-muted">×{c.contracts_qty}</span>
                            </td>
                            <td className="px-3 py-2 font-mono">{c.dte}</td>
                            <td className={cn("px-3 py-2 font-mono", premium >= 0 ? "text-profit" : "text-loss")}>
                              {premium >= 0 ? "+" : ""}
                              {formatCurrency(premium)}
                            </td>
                            <td className="px-3 py-2 font-mono text-profit">
                              {maxProfit == null ? "לא מוגבל" : formatCurrency(maxProfit)}
                            </td>
                            <td className="px-3 py-2 font-mono text-loss">{usd(toNum(c.max_loss_usd))}</td>
                            <td className="px-3 py-2 font-mono">{usd(toNum(c.collateral_required_usd))}</td>
                            <td className="px-3 py-2 font-mono">{pct(c.probability_of_profit)}</td>
                            <td className="px-3 py-2 font-mono">{pct(c.iv_rank, 0)}</td>
                            <td className="px-3 py-2">
                              <FeedTierBadge tier={c.feed_tier} isIndicative={c.is_indicative} />
                            </td>
                            <td className="px-3 py-2">
                              <div className="flex gap-1.5">
                                {inspectBtn(c)}
                                {simulateBtn(c)}
                              </div>
                            </td>
                          </tr>
                          {expanded === c.candidate_id ? (
                            <tr>
                              <td colSpan={11} className="p-2">
                                <CandidateDetail c={c} />
                              </td>
                            </tr>
                          ) : null}
                        </Fragment>
                      );
                    })}
                  </tbody>
                </table>
              </div>

              {/* Mobile cards */}
              <div className="space-y-2 md:hidden">
                {filtered.map((c) => {
                  const premium = toNum(c.estimated_net_premium_total) ?? 0;
                  const maxProfit = toNum(c.max_profit_usd);
                  return (
                    <div key={c.candidate_id} className="rounded-md border border-border-nested bg-surface-inner p-3">
                      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
                        <div className="flex items-center gap-2">
                          <span className="font-mono font-medium">{c.underlying_symbol}</span>
                          <span className="text-sm">{strategyLabel(c.strategy_type)}</span>
                        </div>
                        <Badge variant={c.is_credit ? "success" : "outline"}>{c.is_credit ? "Credit" : "Debit"}</Badge>
                      </div>
                      <div className="grid grid-cols-3 gap-2 text-xs">
                        <Metric label="פרמיה נטו" value={formatCurrency(premium)} tone={premium >= 0 ? "profit" : "loss"} />
                        <Metric label="רווח מקס׳" value={maxProfit == null ? "לא מוגבל" : formatCurrency(maxProfit)} tone="profit" />
                        <Metric label="הפסד מקס׳" value={usd(toNum(c.max_loss_usd))} tone="loss" />
                        <Metric label="בטוחות" value={usd(toNum(c.collateral_required_usd))} />
                        <Metric label="PoP" value={pct(c.probability_of_profit)} />
                        <Metric label="IV Rank · DTE" value={`${pct(c.iv_rank, 0)} · ${c.dte}`} />
                      </div>
                      <div className="mt-2 flex flex-wrap items-center justify-between gap-2">
                        <FeedTierBadge tier={c.feed_tier} isIndicative={c.is_indicative} />
                        <div className="flex gap-1.5">
                          {inspectBtn(c)}
                          {simulateBtn(c)}
                        </div>
                      </div>
                      {expanded === c.candidate_id ? (
                        <div className="mt-2">
                          <CandidateDetail c={c} />
                        </div>
                      ) : null}
                    </div>
                  );
                })}
              </div>
            </>
          )}
        </CardContent>
      </Card>

      {/* AI decisions */}
      <Card>
        <CardHeader className="flex-wrap gap-2">
          <div className="flex flex-wrap items-center gap-2">
            <CardTitle>החלטות AI ונימוקים</CardTitle>
            <Badge variant={groqAvailable ? "info" : "warning"}>
              Groq {groqModel} · {groqAvailable ? "זמין" : "לא זמין"}
            </Badge>
          </div>
          <div className="flex gap-1">
            {(["all", "passed", "rejected"] as const).map((g) => (
              <button
                key={g}
                type="button"
                onClick={() => setGuardFilter(g)}
                className={cn(
                  "rounded px-2.5 py-1 text-xs transition-colors",
                  guardFilter === g
                    ? "bg-primary text-primary-foreground"
                    : "border border-border text-muted hover:bg-surface-active"
                )}
              >
                {g === "all"
                  ? `הכל (${decisions.length})`
                  : g === "passed"
                    ? `עברו Risk Guard (${passedCount})`
                    : `נדחו (${decisions.length - passedCount})`}
              </button>
            ))}
          </div>
        </CardHeader>
        <CardContent>
          {filteredDecisions.length === 0 ? (
            <EmptyState message="אין החלטות עדיין. החלטות AI וחוקים יופיעו לאחר מחזור מנוע." />
          ) : (
            <ul className="space-y-2">
              {filteredDecisions.map((d) => {
                const by = decidedByLabel(d);
                const action = d.action as AIAction;
                return (
                  <li key={d.decision_id} className="rounded-md border border-border-nested bg-surface-inner p-3">
                    <div className="flex flex-wrap items-center gap-2">
                      <Badge variant={ACTION_VARIANT[action] ?? "muted"}>{ACTION_HE[action] ?? d.action}</Badge>
                      <span className="font-mono text-sm font-medium">{d.underlying_symbol}</span>
                      {d.strategy_type ? <span className="text-sm">{strategyLabel(d.strategy_type)}</span> : null}
                      {d.contracts_qty > 1 ? <span className="text-xs text-muted">×{d.contracts_qty}</span> : null}
                      <Badge variant={d.risk_guard_passed ? "success" : "danger"}>
                        Risk Guard · {d.risk_guard_passed ? "אושר" : "נדחה"}
                      </Badge>
                      <Badge variant={by.variant}>{by.label}</Badge>
                      <span className="text-xs text-muted">ביטחון {pct(d.confidence, 0)}</span>
                      <span className="ms-auto text-xs text-muted">{formatDateTime(d.created_at)}</span>
                    </div>
                    {d.rationale_he || d.rationale_en ? (
                      <p className="mt-2 text-sm leading-relaxed text-foreground-secondary">
                        {d.rationale_he || d.rationale_en}
                      </p>
                    ) : null}
                    {d.risk_guard_reasons.length ? (
                      <ul
                        className={cn(
                          "mt-2 space-y-0.5 text-xs",
                          d.risk_guard_passed ? "text-muted" : "text-loss"
                        )}
                      >
                        {d.risk_guard_reasons.map((r) => (
                          <li key={r}>• {r}</li>
                        ))}
                      </ul>
                    ) : null}
                  </li>
                );
              })}
            </ul>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

function Metric({ label, value, tone }: { label: string; value: string; tone?: "profit" | "loss" }) {
  return (
    <div>
      <p className="text-muted">{label}</p>
      <p className={cn("font-mono", tone === "profit" && "text-profit", tone === "loss" && "text-loss")}>{value}</p>
    </div>
  );
}
