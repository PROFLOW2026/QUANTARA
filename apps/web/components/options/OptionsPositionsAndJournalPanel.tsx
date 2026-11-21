"use client";

import { useMemo, useState, type ReactNode } from "react";
import { PnLDisplay } from "@/components/trading/PnLDisplay";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/table";
import { OptionsPayoffChart } from "@/components/options/OptionsPayoffChart";
import type {
  OptionsExpirationItem,
  OptionsJournalEventView,
  OptionsStrategyGroupView,
} from "@/lib/options-types";
import { strategyLabel } from "@/lib/options-types";
import { cn, formatCurrency, formatDateTime, formatPrice } from "@/lib/utils";

export type PositionsSection = "open" | "closed" | "expirations" | "journal";

const STATUS_HE: Record<string, { label: string; variant: "success" | "muted" | "warning" | "danger" | "info" }> = {
  open: { label: "פתוחה", variant: "success" },
  closed: { label: "נסגרה", variant: "muted" },
  expired: { label: "פקעה", variant: "info" },
  assigned: { label: "הוקצתה", variant: "warning" },
  rolled: { label: "גולגלה", variant: "info" },
  rejected: { label: "נדחתה", variant: "danger" },
};

const EVENT_HE: Record<string, { label: string; variant: "success" | "muted" | "warning" | "danger" | "info" | "outline" }> = {
  OPEN: { label: "פתיחה", variant: "info" },
  CLOSE: { label: "סגירה", variant: "muted" },
  ROLL: { label: "גלגול", variant: "warning" },
  EXPIRE: { label: "פקיעה", variant: "success" },
  ASSIGN: { label: "הקצאה", variant: "danger" },
  EXERCISE: { label: "מימוש", variant: "warning" },
  STOCK_DELIVERY: { label: "מסירת מניות", variant: "outline" },
  FEE: { label: "עמלה", variant: "outline" },
};

const WARNING_STYLE: Record<string, string> = {
  urgent: "border-loss-border bg-loss-bg",
  watch: "border-warning-border bg-warning-bg",
  normal: "border-border-nested bg-surface-inner",
};

const WARNING_HE: Record<string, string> = {
  urgent: "דחוף",
  watch: "במעקב",
  normal: "רגיל",
};

function signedUsd(v: number): ReactNode {
  if (v === 0) return <span className="font-mono text-muted">{formatCurrency(0)}</span>;
  return <PnLDisplay value={v} size="sm" />;
}

function StatusBadge({ status }: { status: string }) {
  const s = STATUS_HE[status] ?? { label: status, variant: "muted" as const };
  return <Badge variant={s.variant}>{s.label}</Badge>;
}

// ── Open strategy card ───────────────────────────────────────────────────────

function OpenStrategyCard({
  g,
  onClose,
  onRoll,
  busy,
  spot,
}: {
  g: OptionsStrategyGroupView;
  onClose?: (id: string) => Promise<void> | void;
  onRoll?: (id: string) => Promise<void> | void;
  busy: boolean;
  spot: number | null;
}) {
  const [expanded, setExpanded] = useState(false);
  const [confirm, setConfirm] = useState<"close" | "roll" | null>(null);

  const run = async (kind: "close" | "roll") => {
    setConfirm(null);
    if (kind === "close") await onClose?.(g.id);
    else await onRoll?.(g.id);
  };

  return (
    <Card className={cn(g.pin_risk_warning && "border-warning-border", g.early_assignment_warning && "border-loss-border")}>
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="space-y-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-mono text-base font-semibold">{g.underlying_symbol}</span>
            <span className="text-sm font-medium">{g.strategy_label_he || strategyLabel(g.strategy_type)}</span>
            <StatusBadge status={g.status} />
            <Badge variant={g.is_credit ? "success" : "outline"}>{g.is_credit ? "Credit" : "Debit"}</Badge>
            <Badge variant={g.mode === "ai" ? "info" : "muted"}>{g.mode === "ai" ? "AI" : "חוקים"}</Badge>
            {g.wheel_state ? <Badge variant="outline">Wheel: {g.wheel_state}</Badge> : null}
          </div>
          <p className="text-xs text-muted">
            ×{g.contracts_qty} · פקיעה {g.expiration_date ?? "—"}
            {g.dte != null ? ` (${g.dte} ימים)` : ""} · נפתחה {formatDateTime(g.opened_at)}
          </p>
        </div>
        <div className="text-end">
          <p className="text-xs text-muted">לא ממומש</p>
          <PnLDisplay value={g.unrealized_pnl} size="lg" />
        </div>
      </div>

      {g.pin_risk_warning || g.early_assignment_warning ? (
        <div className="mt-2 flex flex-wrap gap-2">
          {g.pin_risk_warning ? <Badge variant="warning">⚠ סיכון Pin ליד Strike</Badge> : null}
          {g.early_assignment_warning ? <Badge variant="danger">⚠ סיכון הקצאה מוקדמת</Badge> : null}
        </div>
      ) : null}

      <div className="mt-3 grid grid-cols-2 gap-2 text-xs sm:grid-cols-4 lg:grid-cols-8">
        <Cell
          label={g.net_entry_cash_flow >= 0 ? "Credit נכנס" : "Debit ששולם"}
          value={formatCurrency(g.net_entry_cash_flow)}
          tone={g.net_entry_cash_flow >= 0 ? "profit" : "loss"}
        />
        <Cell label="התחייבות פתוחה" value={formatCurrency(g.open_liability_usd)} tone={g.open_liability_usd > 0 ? "loss" : undefined} />
        <Cell label="שווי לונג" value={formatCurrency(g.long_value_usd)} />
        <Cell label="בטוחות נעולות" value={formatCurrency(g.collateral_locked_usd)} tone="warning" />
        <Cell label="רווח מקס׳" value={g.max_profit_usd == null ? "לא מוגבל" : formatCurrency(g.max_profit_usd)} tone="profit" />
        <Cell label="הפסד מקס׳" value={formatCurrency(g.max_loss_usd)} tone="loss" />
        <Cell label="ממומש · עמלות" value={`${formatCurrency(g.realized_pnl)} · ${formatCurrency(g.fees_paid)}`} />
        <Cell label="Δ / Θ / Vega" value={`${g.net_delta.toFixed(2)} / ${g.net_theta.toFixed(2)} / ${g.net_vega.toFixed(2)}`} />
      </div>

      {g.ai_rationale ? (
        <p className="mt-2 rounded-md border border-border-nested bg-surface-inner px-3 py-2 text-xs leading-relaxed text-foreground-secondary">
          <span className="font-medium text-foreground">נימוק: </span>
          {g.ai_rationale}
        </p>
      ) : null}

      <div className="mt-3 flex flex-wrap items-center gap-2">
        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          aria-expanded={expanded}
          className="rounded border border-border px-3 py-1 text-xs text-muted hover:bg-surface-active"
        >
          {expanded ? "הסתר רגליים ו־Payoff" : `רגליים (${g.legs.length}) ו־Payoff`}
        </button>
        {confirm ? (
          <>
            <span className="text-xs text-warning">
              {confirm === "close" ? "לאשר סגירה מדומה במחיר הנוכחי?" : "לאשר גלגול מדומה לפקיעה הבאה?"}
            </span>
            <button
              type="button"
              disabled={busy}
              onClick={() => void run(confirm)}
              className="rounded bg-primary px-3 py-1 text-xs text-primary-foreground hover:bg-primary-hover disabled:opacity-50"
            >
              אישור
            </button>
            <button
              type="button"
              onClick={() => setConfirm(null)}
              className="rounded border border-border px-3 py-1 text-xs text-muted hover:bg-surface-active"
            >
              ביטול
            </button>
          </>
        ) : (
          <>
            <button
              type="button"
              disabled={busy || !onClose}
              onClick={() => setConfirm("close")}
              className="rounded border border-loss-border bg-loss-bg px-3 py-1 text-xs text-loss disabled:opacity-50"
            >
              {busy ? "מעבד…" : "סגירה מדומה"}
            </button>
            <button
              type="button"
              disabled={busy || !onRoll}
              onClick={() => setConfirm("roll")}
              className="rounded border border-warning-border bg-warning-bg px-3 py-1 text-xs text-warning disabled:opacity-50"
            >
              גלגול מדומה
            </button>
          </>
        )}
      </div>

      {expanded ? (
        <div className="mt-3 space-y-3">
          <div className="grid gap-2 sm:grid-cols-2">
            {g.legs.map((leg) => (
              <div key={leg.id} className="rounded-md border border-border-nested bg-surface-inner p-3 text-xs">
                <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
                  <span className="font-mono text-[11px]" dir="ltr">
                    {leg.occ_symbol ?? `${leg.underlying_symbol} (מניה)`}
                  </span>
                  <span className={leg.side === "long" ? "text-profit" : "text-loss"}>
                    {leg.side === "long" ? "Long" : "Short"} · {leg.right ? String(leg.right).toUpperCase() : "STOCK"}
                  </span>
                </div>
                <div className="grid grid-cols-2 gap-x-3 gap-y-1 font-mono tabular-nums">
                  <span className="text-muted">Strike</span>
                  <span className="text-end">{leg.strike != null ? formatPrice(leg.strike) : "—"}</span>
                  <span className="text-muted">כמות</span>
                  <span className="text-end">
                    {leg.quantity}
                    {leg.multiplier !== 100 ? ` ×${leg.multiplier}` : ""}
                  </span>
                  <span className="text-muted">כניסה / Mark</span>
                  <span className="text-end">
                    {formatPrice(leg.entry_price)} / {formatPrice(leg.current_mark)}
                  </span>
                  <span className="text-muted">Bid / Ask</span>
                  <span className="text-end">
                    {leg.bid != null ? formatPrice(leg.bid) : "—"} / {leg.ask != null ? formatPrice(leg.ask) : "—"}
                  </span>
                  <span className="text-muted">Δ / Θ / IV</span>
                  <span className="text-end">
                    {leg.delta != null ? leg.delta.toFixed(2) : "—"} /{" "}
                    {leg.theta != null ? leg.theta.toFixed(3) : "—"} /{" "}
                    {leg.implied_volatility != null
                      ? `${(leg.implied_volatility > 3 ? leg.implied_volatility : leg.implied_volatility * 100).toFixed(1)}%`
                      : "—"}
                  </span>
                  <span className="text-muted">לא ממומש</span>
                  <span className="text-end">{signedUsd(leg.unrealized_pnl)}</span>
                </div>
              </div>
            ))}
          </div>
          <OptionsPayoffChart points={g.payoff_curve} spot={spot} height={240} />
        </div>
      ) : null}
    </Card>
  );
}

function Cell({ label, value, tone }: { label: string; value: string; tone?: "profit" | "loss" | "warning" }) {
  return (
    <div className="rounded-md border border-border-nested bg-surface-inner px-2 py-1.5">
      <p className="text-muted">{label}</p>
      <p
        className={cn(
          "font-mono font-medium",
          tone === "profit" && "text-profit",
          tone === "loss" && "text-loss",
          tone === "warning" && "text-warning"
        )}
      >
        {value}
      </p>
    </div>
  );
}

// ── Expirations calendar ─────────────────────────────────────────────────────

function ExpirationsCalendar({ items }: { items: OptionsExpirationItem[] }) {
  const groups = useMemo(() => {
    const map = new Map<string, OptionsExpirationItem[]>();
    [...items]
      .sort((a, b) => a.expiration_date.localeCompare(b.expiration_date))
      .forEach((it) => map.set(it.expiration_date, [...(map.get(it.expiration_date) ?? []), it]));
    return Array.from(map.entries());
  }, [items]);

  const counts = useMemo(
    () => ({
      urgent: items.filter((i) => i.warning_level === "urgent").length,
      watch: items.filter((i) => i.warning_level === "watch").length,
      itm: items.filter((i) => i.itm_status === "ITM").length,
      pin: items.filter((i) => i.itm_status === "ATM_PIN_RISK").length,
    }),
    [items]
  );

  return (
    <Card>
      <CardHeader className="flex-wrap gap-2">
        <CardTitle>לוח פקיעות והקצאות</CardTitle>
        <div className="flex flex-wrap gap-1.5">
          <Badge variant="danger">דחוף {counts.urgent}</Badge>
          <Badge variant="warning">במעקב {counts.watch}</Badge>
          <Badge variant="danger">ITM {counts.itm}</Badge>
          <Badge variant="warning">Pin Risk {counts.pin}</Badge>
        </div>
      </CardHeader>
      <CardContent>
        {groups.length === 0 ? (
          <EmptyState message="אין פקיעות קרובות לפוזיציות פתוחות." />
        ) : (
          <div className="space-y-4">
            {groups.map(([date, list]) => (
              <div key={date}>
                <div className="mb-2 flex items-center gap-2">
                  <span className="font-mono text-sm font-semibold">{date}</span>
                  <span className="text-xs text-muted">
                    {list[0].dte === 0 ? "פוקע היום" : `${list[0].dte} ימים`}
                  </span>
                  <span className="h-px flex-1 bg-border" />
                </div>
                <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
                  {list.map((it) => (
                    <div
                      key={it.strategy_group_id}
                      className={cn("rounded-md border p-3 text-xs", WARNING_STYLE[it.warning_level] ?? WARNING_STYLE.normal)}
                    >
                      <div className="mb-1 flex flex-wrap items-center justify-between gap-1">
                        <div className="flex items-center gap-2">
                          <span className="font-mono text-sm font-medium">{it.underlying_symbol}</span>
                          <span>{strategyLabel(it.strategy_type)}</span>
                        </div>
                        <Badge
                          variant={it.warning_level === "urgent" ? "danger" : it.warning_level === "watch" ? "warning" : "muted"}
                        >
                          {WARNING_HE[it.warning_level] ?? it.warning_level}
                        </Badge>
                      </div>
                      <div className="flex flex-wrap items-center gap-1.5">
                        <Badge
                          variant={
                            it.itm_status === "ITM" ? "danger" : it.itm_status === "ATM_PIN_RISK" ? "warning" : "success"
                          }
                        >
                          {it.itm_status === "ITM"
                            ? "ITM · צפויה הקצאה/מימוש"
                            : it.itm_status === "ATM_PIN_RISK"
                              ? "ATM · סיכון Pin"
                              : "OTM"}
                        </Badge>
                        <span className="text-muted">
                          Strikes: <span className="font-mono">{it.strikes.map((s) => formatPrice(s)).join(" / ") || "—"}</span>
                        </span>
                      </div>
                      <div className="mt-2 grid grid-cols-3 gap-1">
                        <div>
                          <p className="text-muted">נכס</p>
                          <p className="font-mono">{it.underlying_price != null ? formatPrice(it.underlying_price) : "—"}</p>
                        </div>
                        <div>
                          <p className="text-muted">בטוחות</p>
                          <p className="font-mono text-warning">{formatCurrency(it.collateral_locked_usd)}</p>
                        </div>
                        <div>
                          <p className="text-muted">לא ממומש</p>
                          <PnLDisplay value={it.unrealized_pnl} size="sm" />
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

// ── Journal ──────────────────────────────────────────────────────────────────

function JournalTable({ events }: { events: OptionsJournalEventView[] }) {
  const [type, setType] = useState("all");
  const types = useMemo(() => Array.from(new Set(events.map((e) => e.event_type))), [events]);
  const rows = useMemo(
    () =>
      events
        .filter((e) => type === "all" || e.event_type === type)
        .sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime()),
    [events, type]
  );
  const totals = useMemo(
    () =>
      rows.reduce(
        (acc, e) => ({
          cash: acc.cash + e.cash_delta,
          pnl: acc.pnl + e.realized_pnl_delta,
          collateral: acc.collateral + e.collateral_delta,
          fees: acc.fees + e.fee_usd,
        }),
        { cash: 0, pnl: 0, collateral: 0, fees: 0 }
      ),
    [rows]
  );

  return (
    <Card>
      <CardHeader className="flex-wrap gap-2">
        <CardTitle>יומן חשבונאות וביקורת</CardTitle>
        <div className="flex flex-wrap gap-1">
          {["all", ...types].map((t) => (
            <button
              key={t}
              type="button"
              onClick={() => setType(t)}
              className={cn(
                "rounded px-2.5 py-1 text-xs transition-colors",
                type === t ? "bg-primary text-primary-foreground" : "border border-border text-muted hover:bg-surface-active"
              )}
            >
              {t === "all" ? "הכל" : EVENT_HE[t]?.label ?? t}
            </button>
          ))}
        </div>
      </CardHeader>
      <CardContent className="space-y-3">
        {rows.length === 0 ? (
          <EmptyState message="אין אירועי יומן." />
        ) : (
          <>
            <div className="grid gap-2">
              {rows.map((e) => {
                const ev = EVENT_HE[e.event_type] ?? { label: e.event_type, variant: "muted" as const };
                return (
                  <div key={e.id} className="rounded-md border border-border-nested bg-surface-inner p-3">
                    <div className="mb-2 flex flex-wrap items-center gap-2">
                      <Badge variant={ev.variant}>{ev.label}</Badge>
                      <span className="font-mono text-sm font-medium">{e.underlying_symbol}</span>
                      <span className="text-xs text-muted">{strategyLabel(e.strategy_type)}</span>
                      <span className="ms-auto text-xs text-muted">{formatDateTime(e.created_at)}</span>
                    </div>
                    <div className="grid grid-cols-2 gap-2 text-xs sm:grid-cols-4">
                      <div>
                        <p className="text-muted">שינוי מזומן</p>
                        {signedUsd(e.cash_delta)}
                      </div>
                      <div>
                        <p className="text-muted">רווח ממומש</p>
                        {signedUsd(e.realized_pnl_delta)}
                      </div>
                      <div>
                        <p className="text-muted">בטוחות</p>
                        <p className={cn("font-mono", e.collateral_delta > 0 ? "text-warning" : "text-muted")}>
                          {e.collateral_delta > 0 ? "🔒 " : e.collateral_delta < 0 ? "🔓 " : ""}
                          {formatCurrency(e.collateral_delta)}
                        </p>
                      </div>
                      <div>
                        <p className="text-muted">עמלה</p>
                        <p className="font-mono text-loss">
                          {e.fee_usd ? formatCurrency(-Math.abs(e.fee_usd)) : "—"}
                        </p>
                      </div>
                    </div>
                    {e.description ? (
                      <p className="mt-2 break-words text-xs text-foreground-secondary">{e.description}</p>
                    ) : null}
                  </div>
                );
              })}
            </div>
            <div className="rounded-md border border-border bg-surface-highlight p-3 text-xs font-medium">
              <p className="mb-2">סה״כ ({rows.length} אירועים)</p>
              <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
                <div>
                  <p className="text-muted">מזומן</p>
                  {signedUsd(totals.cash)}
                </div>
                <div>
                  <p className="text-muted">ממומש</p>
                  {signedUsd(totals.pnl)}
                </div>
                <div>
                  <p className="text-muted">בטוחות</p>
                  <p className="font-mono">{formatCurrency(totals.collateral)}</p>
                </div>
                <div>
                  <p className="text-muted">עמלות</p>
                  <p className="font-mono text-loss">{formatCurrency(-Math.abs(totals.fees))}</p>
                </div>
              </div>
            </div>
          </>
        )}
      </CardContent>
    </Card>
  );
}

// ── Panel ────────────────────────────────────────────────────────────────────

type Props = {
  openStrategies: OptionsStrategyGroupView[];
  closedStrategies: OptionsStrategyGroupView[];
  expirations: OptionsExpirationItem[];
  journal: OptionsJournalEventView[];
  sections?: PositionsSection[];
  onClose?: (strategyId: string) => Promise<void> | void;
  onRoll?: (strategyId: string) => Promise<void> | void;
  busyId?: string | null;
};

export function OptionsPositionsAndJournalPanel({
  openStrategies,
  closedStrategies,
  expirations,
  journal,
  sections = ["open", "closed", "expirations", "journal"],
  onClose,
  onRoll,
  busyId,
}: Props) {
  const show = (s: PositionsSection) => sections.includes(s);
  const spotByGroup = useMemo(() => {
    const m = new Map<string, number>();
    expirations.forEach((e) => {
      if (e.underlying_price != null) m.set(e.strategy_group_id, e.underlying_price);
    });
    return m;
  }, [expirations]);
  const openTotals = useMemo(
    () =>
      openStrategies.reduce(
        (acc, g) => ({
          credit: acc.credit + Math.max(g.net_entry_cash_flow, 0),
          liability: acc.liability + g.open_liability_usd,
          collateral: acc.collateral + g.collateral_locked_usd,
          unrealized: acc.unrealized + g.unrealized_pnl,
        }),
        { credit: 0, liability: 0, collateral: 0, unrealized: 0 }
      ),
    [openStrategies]
  );

  return (
    <div className="space-y-4">
      {show("open") ? (
        <section className="space-y-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <p className="text-sm font-medium text-foreground">אסטרטגיות פתוחות ({openStrategies.length})</p>
            {openStrategies.length ? (
              <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted">
                <span>
                  Credit נכנס: <span className="font-mono text-profit">{formatCurrency(openTotals.credit)}</span>
                </span>
                <span>
                  התחייבויות: <span className="font-mono text-loss">{formatCurrency(openTotals.liability)}</span>
                </span>
                <span>
                  בטוחות: <span className="font-mono text-warning">{formatCurrency(openTotals.collateral)}</span>
                </span>
                <span>
                  לא ממומש: <PnLDisplay value={openTotals.unrealized} size="sm" />
                </span>
              </div>
            ) : null}
          </div>
          {openStrategies.length === 0 ? (
            <Card>
              <EmptyState message="אין אסטרטגיות פתוחות בחשבון זה." />
            </Card>
          ) : (
            openStrategies.map((g) => (
              <OpenStrategyCard
                key={g.id}
                g={g}
                onClose={onClose}
                onRoll={onRoll}
                busy={busyId === g.id}
                spot={spotByGroup.get(g.id) ?? null}
              />
            ))
          )}
        </section>
      ) : null}

      {show("closed") ? (
        <Card>
          <CardHeader>
            <CardTitle>אסטרטגיות סגורות ({closedStrategies.length})</CardTitle>
          </CardHeader>
          <CardContent>
            {closedStrategies.length === 0 ? (
              <EmptyState message="אין עדיין אסטרטגיות סגורות." />
            ) : (
              <div className="grid gap-2 sm:grid-cols-2">
                {closedStrategies.map((g) => (
                  <div key={g.id} className="rounded-md border border-border-nested bg-surface-inner p-3">
                    <div className="mb-2 flex flex-wrap items-center gap-2">
                      <span className="font-mono text-sm font-semibold">{g.underlying_symbol}</span>
                      <span className="text-xs">{g.strategy_label_he || strategyLabel(g.strategy_type)}</span>
                      <StatusBadge status={g.status} />
                    </div>
                    <div className="grid grid-cols-2 gap-2 text-xs">
                      <div>
                        <p className="text-muted">נפתחה</p>
                        <p>{formatDateTime(g.opened_at)}</p>
                      </div>
                      <div>
                        <p className="text-muted">נסגרה</p>
                        <p>{g.closed_at ? formatDateTime(g.closed_at) : "—"}</p>
                      </div>
                      <div>
                        <p className="text-muted">Credit/Debit</p>
                        <p className={cn("font-mono", g.net_entry_cash_flow >= 0 ? "text-profit" : "text-loss")}>
                          {formatCurrency(g.net_entry_cash_flow)}
                        </p>
                      </div>
                      <div>
                        <p className="text-muted">ממומש · עמלות</p>
                        <div className="flex flex-wrap items-center gap-2">
                          <PnLDisplay value={g.realized_pnl} size="sm" />
                          <span className="font-mono text-loss">{formatCurrency(-Math.abs(g.fees_paid))}</span>
                        </div>
                      </div>
                    </div>
                    {g.close_reason ? (
                      <p className="mt-2 break-words text-xs text-muted">סיבה: {g.close_reason}</p>
                    ) : null}
                  </div>
                ))}
              </div>
            )}
          </CardContent>
        </Card>
      ) : null}

      {show("expirations") ? <ExpirationsCalendar items={expirations} /> : null}
      {show("journal") ? <JournalTable events={journal} /> : null}
    </div>
  );
}
