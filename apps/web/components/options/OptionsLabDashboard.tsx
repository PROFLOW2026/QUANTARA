"use client";

import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Card, CardContent, SectionPanel } from "@/components/ui/card";
import { OptionsAccountOverview } from "@/components/options/OptionsAccountOverview";
import { FeedTierBadge, OptionsChainPanel, resolveFeedKind } from "@/components/options/OptionsChainPanel";
import { OptionsComparisonPanel } from "@/components/options/OptionsComparisonPanel";
import { OptionsPositionsAndJournalPanel } from "@/components/options/OptionsPositionsAndJournalPanel";
import { OptionsScannerAndAIPanel } from "@/components/options/OptionsScannerAndAIPanel";
import { ApiError } from "@/lib/api-client";
import { optionsApi } from "@/lib/options-api";
import type {
  OptionAccountSummary,
  OptionsControlAction,
  OptionsEngineStatusView,
  OptionsLabOverviewResponse,
  StrategyCandidate,
} from "@/lib/options-types";
import { strategyLabel } from "@/lib/options-types";
import { cn, formatCurrency, formatPercent, formatRelativeTime } from "@/lib/utils";

type TabId = "overview" | "chain" | "scanner" | "calendar" | "compare";

const TABS: { id: TabId; label: string; short: string }[] = [
  { id: "overview", label: "סקירה ופוזיציות", short: "סקירה" },
  { id: "chain", label: "שרשרת אופציות", short: "שרשרת" },
  { id: "scanner", label: "סורק ו־AI", short: "סורק" },
  { id: "calendar", label: "לוח פקיעות ויומן", short: "פקיעות" },
  { id: "compare", label: "השוואת אסטרטגיות", short: "השוואה" },
];

const POLL_MS = 30_000;
const POLL_RUNNING_MS = 8_000;

type Notice = { tone: "success" | "error" | "info"; text: string } | null;

const COHORT_SHORT: Record<string, string> = {
  "options-10k-rules": "Rules · הכל",
  "options-10k-long-only": "Long Only",
  "options-10k-credit-spreads": "Credit Spreads",
  "options-10k-wheel": "Wheel",
  "options-10k-iron-condor": "Iron Condor",
};

function describeError(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.status === 404) return "נתיבי /options טרם חוברו במנוע. המסך יתעדכן אוטומטית כשהם יהיו זמינים.";
    if (e.status >= 500) return `מנוע האופציות אינו זמין כרגע (${e.status}).`;
    return e.message;
  }
  return e instanceof Error ? e.message : "שגיאה לא ידועה";
}

function freshnessLabel(seconds: number | null | undefined): string {
  if (seconds == null) return "רעננות לא ידועה";
  if (seconds < 60) return `עודכן לפני ${Math.round(seconds)} שנ׳`;
  if (seconds < 3600) return `עודכן לפני ${Math.round(seconds / 60)} דק׳`;
  return `עודכן לפני ${(seconds / 3600).toFixed(1)} שע׳`;
}

const PRIMARY_SLUGS = new Set(["options-5k", "options-10k", "options-25k"]);

function isPrimaryAccount(a: OptionAccountSummary): boolean {
  if (PRIMARY_SLUGS.has(a.slug)) return true;
  const g = (a.experiment_group ?? "").toLowerCase();
  return g === "options_lab" && a.mode === "ai";
}

function shortAccountName(a: OptionAccountSummary, primary: boolean): string {
  if (primary) {
    if (a.slug.includes("25k")) return "Options $25K";
    if (a.slug.includes("10k")) return "Options $10K";
    if (a.slug.includes("5k")) return "Options $5K";
  }
  return COHORT_SHORT[a.slug] ?? a.name.replace(/^Options \$10K \(Rules,\s*/i, "").replace(/\)$/, "");
}

function StatusCell({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="min-w-0 rounded-md border border-border/70 bg-surface-inner px-3 py-2">
      <p className="text-[11px] text-muted">{label}</p>
      <div className="mt-1 flex min-w-0 flex-wrap items-center gap-1.5 text-sm text-foreground">{children}</div>
    </div>
  );
}

function EngineHeader({
  status,
  useFixtures,
  onToggleFixtures,
  onControl,
  busy,
}: {
  status: OptionsEngineStatusView | null;
  useFixtures: boolean;
  onToggleFixtures: (v: boolean) => void;
  onControl: (action: OptionsControlAction) => void;
  busy: OptionsControlAction | null;
}) {
  const marketOpen = status?.market_open ?? false;
  const feedKind = resolveFeedKind(status?.feed_tier, status?.is_indicative ?? true);
  const engineLabel = !status
    ? "לא מחובר"
    : status.running_cycle
      ? "מחזור רץ…"
      : status.enabled
        ? "פעיל"
        : "כבוי";
  const engineTone = !status
    ? "text-loss"
    : status.running_cycle
      ? "text-info"
      : status.enabled
        ? "text-profit"
        : "text-muted";

  const btn =
    "inline-flex h-10 min-w-[7.5rem] flex-1 items-center justify-center rounded-md px-4 text-sm font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-50 sm:flex-none";

  return (
    <Card className="space-y-4 overflow-hidden">
      <div className="space-y-1">
        <h2 className="text-lg font-semibold text-foreground">מעבדת אופציות</h2>
        <p className="text-sm text-muted">
          מנוע מבודד — הפעלה כאן אינה משפיעה על Live Sim, Research או AI Trader.
        </p>
      </div>

      <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
        <StatusCell label="סטטוס מנוע">
          <span className={cn("font-medium", engineTone)}>{engineLabel}</span>
        </StatusCell>
        <StatusCell label="סטטוס שוק">
          <span className={marketOpen ? "font-medium text-profit" : "font-medium text-muted"}>
            {marketOpen ? "שוק פתוח" : "שוק סגור"}
          </span>
          {status?.universe?.[0] ? (
            <span className="font-mono text-xs text-muted">{status.universe[0]}</span>
          ) : null}
        </StatusCell>
        <StatusCell label="מקור נתונים">
          <FeedTierBadge
            tier={status?.feed_tier}
            isIndicative={status?.is_indicative ?? true}
            isDelayedTrades={status?.is_delayed_trades ?? false}
          />
        </StatusCell>
        <StatusCell label="מצב החלטה">
          <span className="font-medium">{status?.mode === "ai" ? "AI" : "חוקים"}</span>
          <span className="truncate text-xs text-muted">
            Groq {status?.groq_available ? "זמין" : "גיבוי חוקים"}
          </span>
        </StatusCell>
        <StatusCell label="עדכון אחרון">
          <span className="text-sm">{freshnessLabel(status?.data_freshness_seconds)}</span>
          {status?.last_cycle_at ? (
            <span className="text-xs text-muted">מחזור: {formatRelativeTime(status.last_cycle_at)}</span>
          ) : null}
        </StatusCell>
        <StatusCell label="מוכנות">
          <span className="text-xs text-muted">
            טכנית {status?.readiness_technical ? "✓" : "—"} · שוק{" "}
            {status?.readiness_market_data ? "✓" : "—"} · קדימה{" "}
            {status?.readiness_forward_sim ? "✓" : "—"}
          </span>
        </StatusCell>
      </div>

      <div className="flex flex-col gap-2 sm:flex-row sm:flex-wrap">
        <button
          type="button"
          disabled={!!busy || status?.enabled === true}
          onClick={() => onControl("start")}
          className={cn(btn, "bg-primary text-primary-foreground hover:bg-primary-hover")}
        >
          {busy === "start" ? "מפעיל…" : "הפעלה"}
        </button>
        <button
          type="button"
          disabled={!!busy || status?.enabled === false}
          onClick={() => onControl("stop")}
          className={cn(btn, "border border-loss-border bg-loss-bg text-loss")}
        >
          {busy === "stop" ? "עוצר…" : "עצירה"}
        </button>
        <button
          type="button"
          disabled={!!busy || status?.running_cycle === true}
          onClick={() => onControl("run_once")}
          className={cn(btn, "border border-border text-foreground hover:bg-surface-active")}
        >
          {busy === "run_once" ? "מריץ מחזור…" : "הרצת מחזור יחיד"}
        </button>
      </div>

      <div
        className={cn(
          "rounded-md border px-3 py-3",
          useFixtures || feedKind === "fixture"
            ? "border-info-border bg-info-bg"
            : "border-border bg-surface-inner"
        )}
      >
        <label className="flex cursor-pointer items-start gap-3">
          <input
            type="checkbox"
            checked={useFixtures}
            onChange={(e) => onToggleFixtures(e.target.checked)}
            className="mt-1 accent-current"
          />
          <span className="min-w-0 space-y-0.5">
            <span className={cn("block text-sm font-medium", useFixtures ? "text-info" : "text-foreground")}>
              מצב בדיקה · Fixtures
            </span>
            <span className={cn("block text-xs leading-relaxed", useFixtures ? "text-info" : "text-muted")}>
              סימולציה סגורה עם נתוני דמה. מומלץ כשהשוק סגור. אינו משקף ציטוטי שוק חיים.
            </span>
          </span>
        </label>
      </div>

      {status?.last_error ? (
        <div className="rounded-md border border-loss-border bg-loss-bg px-3 py-2 text-xs text-loss">
          שגיאה אחרונה במנוע: {status.last_error}
        </div>
      ) : null}
    </Card>
  );
}

function AccountCard({
  account,
  active,
  primary,
  onClick,
}: {
  account: OptionAccountSummary;
  active: boolean;
  primary: boolean;
  onClick: () => void;
}) {
  const title = shortAccountName(account, primary);
  const subtitle = primary
    ? account.mode === "ai"
      ? "AI"
      : "חוקים"
    : account.strategy_scope
      ? strategyLabel(account.strategy_scope)
      : "רב־אסטרטגי";

  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={cn(
        "flex min-w-0 w-full flex-col gap-1 rounded-lg border px-3 py-3 text-start transition-colors",
        active
          ? "border-primary bg-primary text-primary-foreground shadow-sm"
          : "border-border bg-surface text-foreground hover:bg-surface-active"
      )}
    >
      <span className="truncate text-sm font-semibold leading-tight">{title}</span>
      <span className={cn("truncate text-xs", active ? "opacity-90" : "text-muted")}>{subtitle}</span>
      <div className="mt-1 flex items-baseline justify-between gap-2">
        <span className={cn("font-mono text-xs tabular-nums", active ? "opacity-90" : "text-muted")}>
          {formatCurrency(account.equity)}
        </span>
        <span
          className={cn(
            "font-mono text-xs tabular-nums",
            active ? "opacity-90" : account.return_pct >= 0 ? "text-profit" : "text-loss"
          )}
        >
          {formatPercent(account.return_pct)}
        </span>
      </div>
    </button>
  );
}

export function OptionsLabDashboard() {
  const [overview, setOverview] = useState<OptionsLabOverviewResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [accountSlug, setAccountSlug] = useState<string | null>(null);
  const [symbol, setSymbol] = useState("SPY");
  const [tab, setTab] = useState<TabId>("overview");
  const [useFixtures, setUseFixtures] = useState(false);
  const [controlBusy, setControlBusy] = useState<OptionsControlAction | null>(null);
  const [positionBusyId, setPositionBusyId] = useState<string | null>(null);
  const [simulatingId, setSimulatingId] = useState<string | null>(null);
  const [notice, setNotice] = useState<Notice>(null);
  const fixturesTouched = useRef(false);

  const load = useCallback(
    async (slug: string | null = accountSlug) => {
      try {
        const res = await optionsApi.getOverview({ account_slug: slug, symbol });
        setOverview(res);
        setError(null);
        if (!slug) {
          const first = res.selected_account?.slug ?? res.accounts[0]?.slug ?? null;
          if (first) setAccountSlug(first);
        }
        if (!fixturesTouched.current && res.engine_status.feed_tier === "fixture") {
          setUseFixtures(true);
        }
      } catch (e) {
        setError(describeError(e));
      } finally {
        setLoading(false);
      }
    },
    [accountSlug, symbol]
  );

  useEffect(() => {
    void load();
  }, [load]);

  const running = overview?.engine_status.running_cycle ?? false;
  useEffect(() => {
    const id = setInterval(() => void load(), running ? POLL_RUNNING_MS : POLL_MS);
    return () => clearInterval(id);
  }, [load, running]);

  useEffect(() => {
    if (!notice) return;
    const id = setTimeout(() => setNotice(null), 6000);
    return () => clearTimeout(id);
  }, [notice]);

  const selectedAccount = useMemo(() => {
    if (!overview) return null;
    if (overview.selected_account && overview.selected_account.slug === accountSlug) return overview.selected_account;
    return overview.accounts.find((a) => a.slug === accountSlug) ?? overview.selected_account ?? null;
  }, [overview, accountSlug]);

  const { primaryAccounts, cohortAccounts } = useMemo(() => {
    const accounts = overview?.accounts ?? [];
    return {
      primaryAccounts: accounts.filter(isPrimaryAccount),
      cohortAccounts: accounts.filter((a) => !isPrimaryAccount(a)),
    };
  }, [overview]);

  const selectAccount = (slug: string) => {
    if (slug === accountSlug) return;
    setAccountSlug(slug);
    setLoading(true);
  };

  const handleControl = async (action: OptionsControlAction) => {
    setControlBusy(action);
    try {
      const res = await optionsApi.control({
        action,
        use_fixtures: useFixtures,
        account_slug: accountSlug,
        symbols: overview?.engine_status.universe?.length ? overview.engine_status.universe : undefined,
      });
      setOverview((prev) => (prev ? { ...prev, engine_status: res.engine_status } : prev));
      setNotice({
        tone: res.ok ? "success" : "error",
        text:
          action === "start"
            ? res.ok
              ? "מנוע האופציות הופעל"
              : "הפעלת המנוע נכשלה"
            : action === "stop"
              ? res.ok
                ? "מנוע האופציות נעצר"
                : "עצירת המנוע נכשלה"
              : res.ok
                ? "מחזור יחיד הושלם"
                : "המחזור נכשל",
      });
      await load();
    } catch (e) {
      setNotice({ tone: "error", text: describeError(e) });
    } finally {
      setControlBusy(null);
    }
  };

  const handleSimulate = async (candidate: StrategyCandidate) => {
    if (!accountSlug) return;
    setSimulatingId(candidate.candidate_id);
    try {
      const g = await optionsApi.simulateOpen(accountSlug, candidate);
      setNotice({
        tone: "success",
        text: `נפתחה פוזיציה מדומה: ${g.strategy_label_he || strategyLabel(g.strategy_type)} על ${g.underlying_symbol}`,
      });
      await load();
    } catch (e) {
      setNotice({ tone: "error", text: describeError(e) });
    } finally {
      setSimulatingId(null);
    }
  };

  const handlePosition = async (kind: "close" | "roll", strategyId: string) => {
    setPositionBusyId(strategyId);
    try {
      const g =
        kind === "close" ? await optionsApi.closePosition(strategyId) : await optionsApi.rollPosition(strategyId);
      setNotice({
        tone: "success",
        text:
          kind === "close"
            ? `נסגרה סימולציה: ${g.underlying_symbol} · רווח ממומש ${g.realized_pnl.toFixed(2)}$`
            : `גולגלה סימולציה: ${g.underlying_symbol} לפקיעה ${g.expiration_date ?? "—"}`,
      });
      await load();
    } catch (e) {
      setNotice({ tone: "error", text: describeError(e) });
    } finally {
      setPositionBusyId(null);
    }
  };

  const status = overview?.engine_status ?? null;

  return (
    <div dir="rtl" className="w-full max-w-full space-y-4">
      <div className="rounded-lg border border-warning-border bg-warning-bg px-4 py-2.5 text-xs leading-relaxed text-warning">
        <span className="font-semibold">סימולציה פרטית בלבד.</span> לא נשלחות פקודות לבורסה או לברוקר. נתונים מסומנים
        במפורש: <span className="font-semibold">Indicative</span> = הערכה/השהיה,{" "}
        <span className="font-semibold">Fixture</span> = נתוני דמה סגורים, <span className="font-semibold">OPRA חי</span>{" "}
        = ציטוטים בזמן אמת. גם עם OPRA — המילויים מדומים ואינם מסחר אמיתי.
      </div>

      <EngineHeader
        status={status}
        useFixtures={useFixtures}
        onToggleFixtures={(v) => {
          fixturesTouched.current = true;
          setUseFixtures(v);
        }}
        onControl={(a) => void handleControl(a)}
        busy={controlBusy}
      />

      {notice ? (
        <div
          role="status"
          className={cn(
            "rounded-md border px-3 py-2 text-sm",
            notice.tone === "success" && "border-profit-border bg-profit-bg text-profit",
            notice.tone === "error" && "border-loss-border bg-loss-bg text-loss",
            notice.tone === "info" && "border-info-border bg-info-bg text-info"
          )}
        >
          {notice.text}
        </div>
      ) : null}

      {error ? (
        <div className="rounded-md border border-loss-border bg-loss-bg px-3 py-2 text-sm text-loss">{error}</div>
      ) : null}

      <SectionPanel className="space-y-4 overflow-hidden">
        <div className="space-y-2">
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <p className="text-sm font-medium text-foreground">חשבונות AI</p>
            <p className="text-xs text-muted">$5K · $10K · $25K</p>
          </div>
          <div className="grid grid-cols-1 gap-2 min-[375px]:grid-cols-3">
            {primaryAccounts.length ? (
              primaryAccounts.map((a) => (
                <AccountCard
                  key={a.slug}
                  account={a}
                  primary
                  active={a.slug === accountSlug}
                  onClick={() => selectAccount(a.slug)}
                />
              ))
            ) : (
              <p className="col-span-full text-xs text-muted">{loading ? "טוען…" : "אין חשבונות"}</p>
            )}
          </div>
        </div>

        {cohortAccounts.length ? (
          <div className="space-y-2 border-t border-border pt-4">
            <div className="flex flex-wrap items-baseline justify-between gap-2">
              <p className="text-sm font-medium text-foreground">קבוצות השוואה</p>
              <p className="text-xs text-muted">$10K · חוקים</p>
            </div>
            <div className="grid grid-cols-1 gap-2 min-[480px]:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5">
              {cohortAccounts.map((a) => (
                <AccountCard
                  key={a.slug}
                  account={a}
                  primary={false}
                  active={a.slug === accountSlug}
                  onClick={() => selectAccount(a.slug)}
                />
              ))}
            </div>
          </div>
        ) : null}
      </SectionPanel>

      <nav
        aria-label="תצוגות אופציות"
        className="grid grid-cols-2 gap-1 rounded-lg border border-border bg-surface-inner p-1 sm:grid-cols-3 lg:grid-cols-5"
      >
        {TABS.map((t) => (
          <button
            key={t.id}
            type="button"
            onClick={() => setTab(t.id)}
            className={cn(
              "min-w-0 rounded-md px-2 py-2.5 text-center text-xs font-medium transition-colors sm:text-sm",
              tab === t.id
                ? "bg-primary text-primary-foreground shadow-sm"
                : "text-muted hover:bg-surface-active hover:text-foreground"
            )}
          >
            <span className="hidden truncate sm:inline">{t.label}</span>
            <span className="truncate sm:hidden">{t.short}</span>
          </button>
        ))}
      </nav>

      {tab === "overview" ? (
        <div className="space-y-4">
          <OptionsAccountOverview account={selectedAccount} loading={loading} />
          <OptionsPositionsAndJournalPanel
            openStrategies={overview?.open_strategies ?? []}
            closedStrategies={overview?.closed_strategies ?? []}
            expirations={overview?.expirations ?? []}
            journal={overview?.journal ?? []}
            sections={["open", "closed"]}
            onClose={(id) => handlePosition("close", id)}
            onRoll={(id) => handlePosition("roll", id)}
            busyId={positionBusyId}
          />
        </div>
      ) : null}

      {tab === "chain" ? (
        <OptionsChainPanel
          key={useFixtures ? "fx" : "live"}
          initialChain={overview?.chain_preview ?? null}
          useFixtures={useFixtures}
          defaultSymbol={symbol}
          onSymbolChange={setSymbol}
        />
      ) : null}

      {tab === "scanner" ? (
        <OptionsScannerAndAIPanel
          opportunities={overview?.opportunities ?? []}
          decisions={overview?.ai_decisions ?? []}
          accountSlug={accountSlug}
          groqModel={status?.groq_model}
          groqAvailable={status?.groq_available}
          onSimulate={handleSimulate}
          simulatingId={simulatingId}
        />
      ) : null}

      {tab === "calendar" ? (
        <OptionsPositionsAndJournalPanel
          openStrategies={overview?.open_strategies ?? []}
          closedStrategies={overview?.closed_strategies ?? []}
          expirations={overview?.expirations ?? []}
          journal={overview?.journal ?? []}
          sections={["expirations", "journal"]}
        />
      ) : null}

      {tab === "compare" ? (
        <OptionsComparisonPanel
          rows={overview?.comparison ?? []}
          selectedSlug={accountSlug}
          onSelectAccount={(slug) => {
            selectAccount(slug);
            setTab("overview");
          }}
        />
      ) : null}

      {loading && overview ? (
        <CardContent className="text-center text-xs text-muted">מרענן…</CardContent>
      ) : null}
    </div>
  );
}
