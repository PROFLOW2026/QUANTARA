"use client";

import { useCallback, useEffect, useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { api, type LiveSimAllocationSettings } from "@/lib/api-client";
import { t } from "@/lib/i18n";
import { formatCurrency } from "@/lib/utils";

export function LiveSimAllocationSettingsPanel() {
  const [settings, setSettings] = useState<LiveSimAllocationSettings | null>(null);
  const [ibkr, setIbkr] = useState("");
  const [kraken, setKraken] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await api.getLiveSimAllocationSettings();
      setSettings(data);
      if (data.available) {
        setIbkr(String(data.ibkr_allocation ?? 0));
        setKraken(String(data.kraken_allocation ?? 0));
      }
    } catch {
      setSettings(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const target = settings?.target_capital ?? 10000;
  const ibkrNum = Number(ibkr) || 0;
  const krakenNum = Number(kraken) || 0;
  const remaining = target - ibkrNum - krakenNum;
  const canActivate = remaining === 0 && ibkrNum >= 0 && krakenNum >= 0;

  async function save(activate: boolean) {
    setSaving(true);
    setMessage(null);
    try {
      const result = await api.updateLiveSimAllocationSettings({
        ibkr_allocation: ibkrNum,
        kraken_allocation: krakenNum,
        activate,
      });
      if (result.ok) {
        setMessage(
          activate
            ? t("home.live_sim_multi_broker_activated")
            : t("home.live_sim_allocation_saved")
        );
        await load();
      } else {
        setMessage(result.error ?? t("common.error"));
      }
    } catch {
      setMessage(t("common.error"));
    } finally {
      setSaving(false);
    }
  }

  if (loading) {
    return <p className="text-muted text-sm">{t("common.loading")}</p>;
  }
  if (!settings?.available) {
    return null;
  }

  if (settings.multi_broker_mode_enabled) {
    return (
      <Card className="mt-4">
        <CardHeader>
          <CardTitle>{t("home.live_sim_allocation_title")}</CardTitle>
        </CardHeader>
        <CardContent className="text-sm text-muted">
          {t("home.live_sim_multi_broker_active")}
        </CardContent>
      </Card>
    );
  }

  return (
    <Card className="mt-4">
      <CardHeader>
        <CardTitle>{t("home.live_sim_allocation_title")}</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4 text-sm">
        <p>{t("home.live_sim_allocation_intro")}</p>
        <p className="font-medium">
          {t("home.live_sim_total_capital")}: {formatCurrency(target)}
        </p>
        <div className="grid gap-3 sm:grid-cols-2">
          <label className="space-y-1">
            <span>{t("home.live_sim_ibkr_allocation")}</span>
            <input
              type="number"
              min={0}
              step={100}
              value={ibkr}
              onChange={(e) => setIbkr(e.target.value)}
              className="w-full rounded-md border border-border bg-background px-3 py-2 font-mono"
            />
          </label>
          <label className="space-y-1">
            <span>{t("home.live_sim_kraken_allocation")}</span>
            <input
              type="number"
              min={0}
              step={100}
              value={kraken}
              onChange={(e) => setKraken(e.target.value)}
              className="w-full rounded-md border border-border bg-background px-3 py-2 font-mono"
            />
          </label>
        </div>
        <p className={remaining === 0 ? "text-success" : "text-warning"}>
          {t("home.live_sim_allocation_remaining")}: {formatCurrency(remaining)}
        </p>
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            disabled={saving}
            onClick={() => void save(false)}
            className="rounded-md border border-border px-4 py-2 hover:bg-surface-inner-hover-soft disabled:opacity-50"
          >
            {t("home.live_sim_save_allocation")}
          </button>
          <button
            type="button"
            disabled={saving || !canActivate}
            onClick={() => void save(true)}
            className="rounded-md bg-accent px-4 py-2 text-accent-foreground disabled:opacity-50"
          >
            {t("home.live_sim_activate_multi_broker")}
          </button>
        </div>
        {!canActivate ? (
          <p className="text-xs text-muted">{t("home.live_sim_allocation_must_match")}</p>
        ) : null}
        {message ? <p className="text-sm">{message}</p> : null}
      </CardContent>
    </Card>
  );
}
