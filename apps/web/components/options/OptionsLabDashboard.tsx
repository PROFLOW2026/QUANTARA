"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, SectionPanel } from "@/components/ui/card";
import { Tabs } from "@/components/ui/tabs";
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
import { cn, formatPercent, formatRelativeTime } from "@/lib/utils";

type TabId = "overview" | "chain" | "scanner" | "calendar" | "compare";

const TABS: { id: TabId; label: string }[] = [
  { id: "overview", label: "סקירה ופוזיציות" },
  { id: "chain", label: "שרשרת אופציות" },
  { id: "scanner", label: "סורק ו־AI" },
  { id: "calendar", label: "לוח פקיעות ויומן" },
  { id: "compare", label: "השוואת אסטרטגיות" },
];

const POLL_MS = 30_000;
const POLL_RUNNING_MS = 8_000;

type Notice = { tone: "success" | "error" | "info"; text: string } | null;

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

function isPrimaryAccount(a: OptionAccountSummary): boolean {
  const g = (a.experiment_group ?? "").toLowerCase();
  return !g || g === "core" || g === "primary" || g === "capital" || g === "options_lab";
}

function ReadinessDot({ ok, label }: { ok: boolean; label: string }) {
  return (
    <span className="inline-flex items-center gap-1 text-xs text-muted">
      <span className={cn("h-2 w-2 rounded-full", ok ? "bg-profit" : "bg-warning")} />
      {label}
    </span>
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
  const engineBadge = !status
    ? { label: "מנוע לא מחובר", variant: "danger" as const }
    : status.running_cycle
      ? { label: "מחזור רץ…", variant: "info" as const }
      : status.enabled
        ? { label: "מנוע אופציות פעיל", variant: "success" as const }
        : { label: "מנוע אופציות כבוי", variant: "muted" as const };

  const btn =
    "rounded px-3 py-1.5 text-sm font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-50";

  return (
    <Card className="space-y-3">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="space-y-1">
          <h2 className="text-lg font-semibold text-foreground">מעבדת אופציות · Options Lab</h2>
          <p className="text-xs text-muted">
            מנוע אופציות מבודד — הפעלה/עצירה כאן אינה משפיעה על Live Sim, Research או AI Trader.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
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
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <Badge variant={engineBadge.variant}>{engineBadge.label}</Badge>
        {status ? (
          <Badge variant={status.mode === "ai" ? "info" : "muted"}>
            {status.mode === "ai" ? "מצב החלטה: AI" : "מצב החלטה: חוקים"}
          </Badge>
        ) : null}
        <FeedTierBadge
          tier={status?.feed_tier}
          isIndicative={status?.is_indicative ?? true}
          isDelayedTrades={status?.is_delayed_trades ?? false}
        />
        <Badge variant={marketOpen ? "success" : "muted"}>{marketOpen ? "שוק פתוח" : "שוק סגור"}</Badge>
        <span className="text-xs text-muted">{freshnessLabel(status?.data_freshness_seconds)}</span>
        <Badge variant={status?.groq_available ? "info" : "warning"}>
          Groq {status?.groq_model ?? "openai/gpt-oss-120b"} · {status?.groq_available ? "זמין" : "לא זמין — גיבוי חוקים"}
        </Badge>
        {status ? <span className="text-xs text-muted">{status.groq_calls_today} קריאות היום</span> : null}
      </div>

      <div className="flex flex-wrap items-center justify-between gap-3 border-t border-border pt-3">
        <div className="flex flex-wrap items-center gap-3">
          <ReadinessDot ok={status?.readiness_technical ?? false} label="מוכנות טכנית" />
          <ReadinessDot ok={status?.readiness_market_data ?? false} label="נתוני שוק" />
          <ReadinessDot ok={status?.readiness_forward_sim ?? false} label="סימולציה קדימה" />
          {status?.last_cycle_at ? (
            <span className="text-xs text-muted">מחזור אחרון: {formatRelativeTime(status.last_cycle_at)}</span>
          ) : null}
          {status?.universe?.length ? (
            <span className="font-mono text-xs text-muted">{status.universe.join(" · ")}</span>
          ) : null}
        </div>
        <label
          className={cn(
            "inline-flex cursor-pointer items-center gap-2 rounded-md border px-3 py-1.5 text-xs",
            !marketOpen ? "border-info-border bg-info-bg text-info" : "border-border text-muted"
          )}
        >
          <input
            type="checkbox"
            checked={useFixtures}
            onChange={(e) => onToggleFixtures(e.target.checked)}
            className="accent-current"
          />
          שימוש ב־Fixtures (סימולציה סגורה)
          {!marketOpen ? <span className="opacity-80">· מומלץ כשהשוק סגור</span> : null}
        </label>
      </div>

      {status?.last_error ? (
        <div className="rounded-md border border-loss-border bg-loss-bg px-3 py-2 text-xs text-loss">
          שגיאה אחרונה במנוע: {status.last_error}
        </div>
      ) : null}
      {useFixtures || feedKind === "fixture" ? (
        <div className="rounded-md border border-info-border bg-info-bg px-3 py-2 text-xs text-info">
          מצב Fixture פעיל — כל הציטוטים, הסריקות והעסקאות מבוססים על נתוני דמה סגורים ואינם משקפים את השוק.
        </div>
      ) : null}
    </Card>
  );
}

function AccountPill({
  account,
  active,
  onClick,
}: {
  account: OptionAccountSummary;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "flex shrink-0 items-center gap-2 rounded-full px-3 py-1.5 text-sm transition-colors",
        active
          ? "bg-primary text-primary-foreground"
          : "border border-border bg-surface text-foreground hover:bg-surface-active"
      )}
      title={account.strategy_scope ? strategyLabel(account.strategy_scope) : "רב־אסטרטגי"}
    >
      <span className="font-medium">{account.name}</span>
      <span
        className={cn(
          "font-mono text-xs",
          active ? "opacity-90" : account.return_pct >= 0 ? "text-profit" : "text-loss"
        )}
      >
        {formatPercent(account.return_pct)}
      </span>
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
            ? res.ok ? "מנוע האופציות הופעל" : "הפעלת המנוע נכשלה"
            : action === "stop"
              ? res.ok ? "מנוע האופציות נעצר" : "עצירת המנוע נכשלה"
              : res.ok ? "מחזור יחיד הושלם" : "המחזור נכשל",
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
    <div dir="rtl" className="space-y-4">
      {/* Disclaimer */}
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

      {/* Accounts */}
      <SectionPanel className="space-y-2">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-xs text-muted">חשבונות הון:</span>
          <div className="flex gap-2 overflow-x-auto pb-1 scrollbar-thin">
            {primaryAccounts.length ? (
              primaryAccounts.map((a) => (
                <AccountPill key={a.slug} account={a} active={a.slug === accountSlug} onClick={() => selectAccount(a.slug)} />
              ))
            ) : (
              <span className="text-xs text-muted">{loading ? "טוען…" : "אין חשבונות"}</span>
            )}
          </div>
        </div>
        {cohortAccounts.length ? (
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-xs text-muted">קבוצות השוואה:</span>
            <div className="flex gap-2 overflow-x-auto pb-1 scrollbar-thin">
              {cohortAccounts.map((a) => (
                <AccountPill key={a.slug} account={a} active={a.slug === accountSlug} onClick={() => selectAccount(a.slug)} />
              ))}
            </div>
          </div>
        ) : null}
      </SectionPanel>

      {/* Sub-navigation */}
      <div className="overflow-x-auto scrollbar-thin">
        <Tabs
          tabs={TABS}
          active={tab}
          onChange={(id) => setTab(id as TabId)}
          className="min-w-max whitespace-nowrap"
        />
      </div>

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
