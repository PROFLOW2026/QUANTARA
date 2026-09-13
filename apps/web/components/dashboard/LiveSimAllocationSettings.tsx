"use client";

import { useCallback, useEffect, useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { api, type LiveSimAllocationSettings } from "@/lib/api-client";
import { t } from "@/lib/i18n";
import { formatCurrency } from "@/lib/utils";

function AssetRow({
  label,
  amount,
}: {
  label: string;
  amount: number;
}) {
  return (
    <div className="flex items-center justify-between border-b border-border/50 py-1.5 last:border-0">
      <span>{label}</span>
      <span className="font-mono">{formatCurrency(amount)}</span>
    </div>
  );
}

export function LiveSimAllocationSettingsPanel() {
  const [settings, setSettings] = useState<LiveSimAllocationSettings | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await api.getLiveSimAllocationSettings();
      setSettings(data);
    } catch {
      setSettings(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function saveDraft() {
    setSaving(true);
    setMessage(null);
    try {
      const result = await api.updateLiveSimAllocationSettings({ activate: false });
      if (result.ok) {
        setMessage(t("home.live_sim_equal_asset_saved"));
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

  const target = settings.target_capital ?? 10000;
  const perAsset = settings.per_asset_capital ?? 1250;
  const ibkrAssets =
    settings.assets?.filter((a) => a.broker_vendor === "IBKR") ?? [];
  const krakenAssets =
    settings.assets?.filter((a) => a.broker_vendor === "KRAKEN") ?? [];

  if (settings.multi_broker_mode_enabled && settings.equal_asset_allocation_enabled) {
    return (
      <Card className="mt-4">
        <CardHeader>
          <CardTitle>{t("home.live_sim_equal_asset_title")}</CardTitle>
        </CardHeader>
        <CardContent className="text-sm text-muted">
          {t("home.live_sim_equal_asset_active")}
        </CardContent>
      </Card>
    );
  }

  return (
    <Card className="mt-4">
      <CardHeader>
        <CardTitle>{t("home.live_sim_equal_asset_title")}</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4 text-sm">
        <p>{t("home.live_sim_equal_asset_intro")}</p>
        <p className="font-medium">
          {t("home.live_sim_total_capital")}: {formatCurrency(target)}
        </p>
        <p className="text-muted">{t("home.live_sim_equal_asset_per_asset", { amount: formatCurrency(perAsset) })}</p>

        <div className="rounded-md border border-border p-3">
          <p className="mb-2 font-medium">
            IBKR-like: {formatCurrency(settings.ibkr_derived_total ?? 7500)}
          </p>
          {ibkrAssets.length > 0
            ? ibkrAssets.map((a) => (
                <AssetRow key={a.canonical_symbol} label={a.label_he} amount={perAsset} />
              ))
            : (
              <>
                <AssetRow label="NVDA" amount={perAsset} />
                <AssetRow label="TSLA" amount={perAsset} />
                <AssetRow label="AMD" amount={perAsset} />
                <AssetRow label="COIN" amount={perAsset} />
                <AssetRow label={t("home.live_sim_asset_gold")} amount={perAsset} />
                <AssetRow label="GBP/JPY" amount={perAsset} />
              </>
            )}
        </div>

        <div className="rounded-md border border-border p-3">
          <p className="mb-2 font-medium">
            Kraken-like: {formatCurrency(settings.kraken_derived_total ?? 2500)}
          </p>
          {krakenAssets.length > 0
            ? krakenAssets.map((a) => (
                <AssetRow key={a.canonical_symbol} label={a.label_he} amount={perAsset} />
              ))
            : (
              <>
                <AssetRow label="Bitcoin" amount={perAsset} />
                <AssetRow label="Ethereum" amount={perAsset} />
              </>
            )}
        </div>

        {settings.legacy_audit?.requires_manual_attribution_review ? (
          <p className="text-xs text-warning">{t("home.live_sim_equal_asset_legacy_audit")}</p>
        ) : null}

        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            disabled={saving}
            onClick={() => void saveDraft()}
            className="rounded-md border border-border px-4 py-2 hover:bg-surface-inner-hover-soft disabled:opacity-50"
          >
            {t("home.live_sim_save_equal_asset")}
          </button>
        </div>
        <p className="text-xs text-muted">{t("home.live_sim_equal_asset_not_auto_activate")}</p>
        {message ? <p className="text-sm">{message}</p> : null}
      </CardContent>
    </Card>
  );
}
