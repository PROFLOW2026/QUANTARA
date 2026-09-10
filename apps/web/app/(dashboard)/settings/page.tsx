"use client";

import { useCallback, useEffect, useState } from "react";
import { PageHeader, ErrorBanner } from "@/components/layout/PageHeader";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { translateRiskProfile } from "@/lib/display-text";
import { OwnerTradingControls } from "@/components/trading/OwnerTradingControls";
import { api, ApiError, type Settings } from "@/lib/api-client";
import { t } from "@/lib/i18n";

export default function SettingsPage() {
  const [settings, setSettings] = useState<Settings | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setSettings(await api.getSettings());
      setError(null);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : t("common.error"));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  async function handleHaltToggle() {
    if (!settings) return;
    setSaving(true);
    try {
      if (settings.trading_halted) {
        await api.resumeTrading();
      } else {
        await api.haltTrading();
      }
      await load();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : t("common.error"));
    } finally {
      setSaving(false);
    }
  }

  if (loading) {
    return (
      <>
        <PageHeader titleKey="settings.title" />
        <p className="text-muted">{t("common.loading")}</p>
      </>
    );
  }

  return (
    <>
      <PageHeader titleKey="settings.title" />
      {error && <div className="mb-4"><ErrorBanner message={error} /></div>}

      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader><CardTitle>{t("settings.timezone")}</CardTitle></CardHeader>
          <CardContent>
            <p className="font-mono">{settings?.timezone ?? "—"}</p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader><CardTitle>{t("settings.default_risk_profile")}</CardTitle></CardHeader>
          <CardContent>
            <p>{translateRiskProfile(settings?.default_risk_profile)}</p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader><CardTitle>{t("settings.paper_trading")}</CardTitle></CardHeader>
          <CardContent>
            <p>{settings?.paper_trading_enabled ? t("settings.paper_enabled") : t("common.inactive")}</p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader><CardTitle>{t("settings.initial_capital")}</CardTitle></CardHeader>
          <CardContent>
            <p className="font-mono">${settings?.initial_capital?.toLocaleString() ?? "—"}</p>
            <p className="mt-1 text-xs text-muted">{t("settings.initial_capital_readonly")}</p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader><CardTitle>{t("settings.trading_control")}</CardTitle></CardHeader>
          <CardContent className="space-y-4">
            <OwnerTradingControls />
            <button
              onClick={handleHaltToggle}
              disabled={saving}
              className={`rounded-md px-4 py-2 text-sm font-medium text-white disabled:opacity-50 ${
                settings?.trading_halted
                  ? "bg-profit hover:bg-profit/90"
                  : "bg-loss hover:bg-loss/90"
              }`}
            >
              {settings?.trading_halted
                ? t("settings.resume_trading")
                : t("settings.halt_trading")}
            </button>
          </CardContent>
        </Card>

        <Card>
          <CardHeader><CardTitle>{t("settings.execution_defaults")}</CardTitle></CardHeader>
          <CardContent className="space-y-1 text-sm font-mono">
            <p>{t("settings.spread")}: {settings?.execution_defaults?.spread ?? "—"}</p>
            <p>{t("settings.slippage")}: {settings?.execution_defaults?.slippage ?? "—"}</p>
            <p>{t("settings.fees")}: {settings?.execution_defaults?.fees ?? "—"}</p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader><CardTitle>{t("settings.api_key")}</CardTitle></CardHeader>
          <CardContent>
            <code className="break-all text-xs text-muted">
              {settings?.api_key_display ?? "••••••••"}
            </code>
          </CardContent>
        </Card>

        <Card>
          <CardHeader><CardTitle>{t("settings.language")}</CardTitle></CardHeader>
          <CardContent>
            <p>{t("settings.language_hebrew")}</p>
          </CardContent>
        </Card>
      </div>
    </>
  );
}
