"use client";

import { HomeSummaryCard, HomeSummaryValue } from "@/components/dashboard/HomeSummaryCard";
import { PnLDisplay } from "@/components/trading/PnLDisplay";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle, HighlightCard, SectionPanel } from "@/components/ui/card";
import type { OptionAccountSummary, WheelState } from "@/lib/options-types";
import { strategyLabel, toNum } from "@/lib/options-types";
import { cn, formatCurrency, formatPercent, formatRiskPercent } from "@/lib/utils";

const WHEEL_STATE_HE: Record<WheelState, string> = {
  idle: "ממתין (ללא פוזיציה)",
  short_put: "שלב 1 · PUT מכוסה מזומן",
  holding_shares: "שלב 2 · מחזיק מניות (הוקצה)",
  short_call: "שלב 3 · Covered Call",
};

const WHEEL_STATE_VARIANT: Record<WheelState, "muted" | "info" | "warning" | "success"> = {
  idle: "muted",
  short_put: "info",
  holding_shares: "warning",
  short_call: "success",
};

type Props = {
  account: OptionAccountSummary | null;
  loading?: boolean;
};

function SectionHeading({ title, hint }: { title: string; hint?: string }) {
  return (
    <div className="mb-2 flex flex-wrap items-baseline justify-between gap-2">
      <p className="text-sm font-medium text-foreground">{title}</p>
      {hint ? <p className="text-xs text-muted">{hint}</p> : null}
    </div>
  );
}

export function OptionsAccountOverview({ account, loading }: Props) {
  if (loading && !account) {
    return <p className="text-muted">טוען נתוני חשבון…</p>;
  }
  if (!account) {
    return (
      <Card>
        <CardContent className="py-6 text-center text-sm text-muted">
          לא נבחר חשבון אופציות או שהמנוע עדיין לא החזיר נתונים.
        </CardContent>
      </Card>
    );
  }

  const a = account;
  const reservedTotal = a.cash_reserved + a.margin_used;
  const freePct = a.cash_balance > 0 ? (a.free_cash / a.cash_balance) * 100 : 0;
  const unrealizedPremium = a.premium_collected_gross - a.premium_realized_net;
  const realizedShare =
    a.premium_collected_gross > 0
      ? Math.min(Math.max(a.premium_realized_net / a.premium_collected_gross, 0), 1) * 100
      : 0;
  const liabilityShare =
    a.premium_collected_gross > 0
      ? Math.min(a.short_options_liability / a.premium_collected_gross, 1) * 100
      : 0;
  const totalPnl = a.realized_pnl + a.unrealized_pnl;
  const wheelEntries = Object.entries(a.wheel_states ?? {});
  const rp = a.rule_profile;

  return (
    <div className="space-y-4">
      {/* Account identity */}
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-base font-semibold text-foreground">{a.name}</span>
        <Badge variant={a.mode === "ai" ? "info" : "muted"}>
          {a.mode === "ai" ? "מצב AI" : "מצב חוקים"}
        </Badge>
        <Badge variant="outline">
          {a.strategy_scope ? strategyLabel(a.strategy_scope) : "רב־אסטרטגי"}
        </Badge>
        {a.experiment_group ? <Badge variant="outline">קבוצה: {a.experiment_group}</Badge> : null}
        {!a.is_active ? <Badge variant="warning">לא פעיל</Badge> : null}
        <span className="text-xs text-muted">הון התחלתי {formatCurrency(a.starting_capital)}</span>
      </div>

      {/* Equity / return / drawdown */}
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <HighlightCard>
          <CardHeader className="mb-2">
            <CardTitle>הון (Equity)</CardTitle>
          </CardHeader>
          <CardContent>
            <HomeSummaryValue>{formatCurrency(a.equity)}</HomeSummaryValue>
            <p className="mt-1 text-xs text-muted">מזומן + שווי לונג + מניות − התחייבויות</p>
          </CardContent>
        </HighlightCard>
        <HomeSummaryCard label="תשואה מצטברת">
          <HomeSummaryValue className={a.return_pct >= 0 ? "text-profit" : "text-loss"}>
            {formatPercent(a.return_pct)}
          </HomeSummaryValue>
          <div className="mt-1">
            <PnLDisplay value={totalPnl} size="sm" />
          </div>
        </HomeSummaryCard>
        <HomeSummaryCard label="Drawdown מקסימלי">
          <HomeSummaryValue className="text-loss">{formatRiskPercent(a.max_drawdown_pct)}</HomeSummaryValue>
          <p className="mt-1 text-xs text-muted">{formatCurrency(a.max_drawdown_usd)}</p>
        </HomeSummaryCard>
        <HomeSummaryCard label="ביצועים">
          <HomeSummaryValue>
            {a.win_rate_pct != null ? `${a.win_rate_pct.toFixed(1)}%` : "—"}
            <span className="ms-2 text-xs text-muted">
              PF {a.profit_factor != null ? (a.profit_factor >= 999 ? "∞" : a.profit_factor.toFixed(2)) : "—"}
            </span>
          </HomeSummaryValue>
          <p className="mt-1 text-xs text-muted">
            {a.open_strategies_count} פתוחות · {a.closed_strategies_count} סגורות
          </p>
        </HomeSummaryCard>
      </div>

      {/* Cash */}
      <SectionPanel>
        <SectionHeading title="מזומן ובטוחות" hint="בטוחות נעולות אינן זמינות לפתיחת עסקאות חדשות" />
        <div className="grid gap-3 sm:grid-cols-3">
          <HomeSummaryCard label="מזומן כולל (Cash)">
            <HomeSummaryValue>{formatCurrency(a.cash_balance)}</HomeSummaryValue>
          </HomeSummaryCard>
          <HomeSummaryCard label="בטוחות נעולות (Reserved / Margin)">
            <HomeSummaryValue className="text-warning">{formatCurrency(reservedTotal)}</HomeSummaryValue>
            <p className="mt-1 text-xs text-muted">
              מזומן שמור {formatCurrency(a.cash_reserved)} · מרג׳ין {formatCurrency(a.margin_used)}
            </p>
          </HomeSummaryCard>
          <HighlightCard>
            <CardHeader className="mb-2">
              <CardTitle>מזומן פנוי (Free Cash)</CardTitle>
            </CardHeader>
            <CardContent>
              <HomeSummaryValue className={a.free_cash > 0 ? "text-profit" : "text-loss"}>
                {formatCurrency(a.free_cash)}
              </HomeSummaryValue>
              <div className="mt-2 h-1.5 w-full overflow-hidden rounded-full bg-surface-inner">
                <div
                  className="h-full rounded-full bg-profit"
                  style={{ width: `${Math.min(Math.max(freePct, 0), 100)}%` }}
                />
              </div>
              <p className="mt-1 text-xs text-muted">{formatRiskPercent(freePct)} מהמזומן הכולל</p>
            </CardContent>
          </HighlightCard>
        </div>
      </SectionPanel>

      {/* Premium accounting */}
      <SectionPanel>
        <SectionHeading
          title="חשבונאות פרמיות"
          hint="Credit שנכנס אינו רווח עד שהפוזיציה נסגרת, פוקעת או מוקצית"
        />
        <div className="grid gap-3 sm:grid-cols-3">
          <HomeSummaryCard label="פרמיות שהתקבלו (Credit נכנס · ברוטו)">
            <HomeSummaryValue>{formatCurrency(a.premium_collected_gross)}</HomeSummaryValue>
            <p className="mt-1 text-xs text-muted">סך הקרדיט שנגבה בפתיחת פוזיציות שורט</p>
          </HomeSummaryCard>
          <HomeSummaryCard label="פרמיות שהפכו לרווח ממומש (נטו)">
            <HomeSummaryValue className={a.premium_realized_net >= 0 ? "text-profit" : "text-loss"}>
              {formatCurrency(a.premium_realized_net)}
            </HomeSummaryValue>
            <p className="mt-1 text-xs text-muted">לאחר עלות סגירה, עמלות והקצאות</p>
          </HomeSummaryCard>
          <HomeSummaryCard label="התחייבויות פתוחות (Option Liability)">
            <HomeSummaryValue className="text-loss">{formatCurrency(a.short_options_liability)}</HomeSummaryValue>
            <p className="mt-1 text-xs text-muted">עלות קנייה חזרה של כל רגלי השורט כעת</p>
          </HomeSummaryCard>
        </div>
        <div className="mt-3 rounded-md border border-border-nested bg-surface-inner px-3 py-2">
          <div className="mb-1 flex flex-wrap justify-between gap-2 text-xs text-muted">
            <span>מומש מתוך ברוטו: {formatRiskPercent(realizedShare)}</span>
            <span>טרם מומש: {formatCurrency(unrealizedPremium)}</span>
            <span>התחייבות ביחס לברוטו: {formatRiskPercent(liabilityShare)}</span>
          </div>
          <div className="relative h-2 w-full overflow-hidden rounded-full bg-surface-active">
            <div className="absolute inset-y-0 start-0 bg-profit" style={{ width: `${realizedShare}%` }} />
            <div
              className="absolute inset-y-0 end-0 bg-loss opacity-60"
              style={{ width: `${liabilityShare}%` }}
            />
          </div>
        </div>
      </SectionPanel>

      {/* PnL / holdings / fees */}
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
        <HomeSummaryCard label="רווח/הפסד לא ממומש">
          <PnLDisplay value={a.unrealized_pnl} size="lg" />
        </HomeSummaryCard>
        <HomeSummaryCard label="רווח/הפסד ממומש">
          <PnLDisplay value={a.realized_pnl} size="lg" />
        </HomeSummaryCard>
        <HomeSummaryCard label="עמלות">
          <HomeSummaryValue className="text-loss">{formatCurrency(-Math.abs(a.fees_paid))}</HomeSummaryValue>
        </HomeSummaryCard>
        <HomeSummaryCard label="שווי אופציות לונג">
          <HomeSummaryValue>{formatCurrency(a.long_options_market_value)}</HomeSummaryValue>
        </HomeSummaryCard>
        <HomeSummaryCard label="שווי מניות מוחזקות">
          <HomeSummaryValue>{formatCurrency(a.stock_holdings_value)}</HomeSummaryValue>
        </HomeSummaryCard>
      </div>

      {/* Wheel + rules */}
      <div className="grid gap-3 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>מצב Wheel לפי נכס</CardTitle>
          </CardHeader>
          <CardContent>
            {wheelEntries.length === 0 ? (
              <p className="text-sm text-muted">אין מחזורי Wheel פעילים בחשבון זה.</p>
            ) : (
              <ul className="grid gap-2 sm:grid-cols-2">
                {wheelEntries.map(([symbol, state]) => {
                  const known = state as WheelState;
                  return (
                    <li
                      key={symbol}
                      className="flex items-center justify-between gap-2 rounded-md border border-border-nested bg-surface-inner px-3 py-2"
                    >
                      <span className="font-mono text-sm font-medium">{symbol}</span>
                      <Badge variant={WHEEL_STATE_VARIANT[known] ?? "muted"}>
                        {WHEEL_STATE_HE[known] ?? state}
                      </Badge>
                    </li>
                  );
                })}
              </ul>
            )}
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>פרופיל חוקים ({rp.profile_name})</CardTitle>
          </CardHeader>
          <CardContent className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs">
            <RuleRow label="רמת אישור אופציות" value={String(rp.options_approval_level)} />
            <RuleRow label="מרג׳ין" value={rp.margin_enabled ? "מופעל" : "כבוי"} />
            <RuleRow label="סיכון מקס׳ לעסקה" value={`${toNum(rp.max_risk_per_trade_pct) ?? "—"}%`} />
            <RuleRow label="בטוחות מקס׳" value={`${toNum(rp.max_collateral_pct) ?? "—"}%`} />
            <RuleRow label="אסטרטגיות פתוחות מקס׳" value={String(rp.max_open_strategies)} />
            <RuleRow label="הפסד יומי מקס׳" value={`${toNum(rp.max_daily_loss_pct) ?? "—"}%`} />
            <RuleRow label="Drawdown מקס׳" value={`${toNum(rp.max_drawdown_pct) ?? "—"}%`} />
            <RuleRow label="כלל PDT" value={rp.enforce_pdt_rule ? "נאכף" : "לא נאכף"} />
            <RuleRow
              label="עמלה לחוזה"
              value={formatCurrency(
                (toNum(rp.commission_per_contract_usd) ?? 0) + (toNum(rp.clearing_fee_per_contract_usd) ?? 0)
              )}
            />
            <RuleRow
              label="סגירת ITM לפני פקיעה"
              value={`${rp.auto_close_itm_before_expiry_minutes} דק׳`}
            />
          </CardContent>
        </Card>
      </div>
    </div>
  );
}

function RuleRow({ label, value, className }: { label: string; value: string; className?: string }) {
  return (
    <div className={cn("flex justify-between gap-2 border-b border-border/40 py-1", className)}>
      <span className="text-muted">{label}</span>
      <span className="font-mono text-foreground">{value}</span>
    </div>
  );
}
