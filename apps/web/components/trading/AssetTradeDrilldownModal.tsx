"use client";

import { useEffect, useState } from "react";
import { SimpleModal } from "@/components/ui/SimpleModal";
import { PnLDisplay } from "@/components/trading/PnLDisplay";
import {
  api,
  type AssetAnalyticsRow,
  type CompetitionResponse,
  type Trade,
} from "@/lib/api-client";
import {
  extractAssetFromPortfolioName,
  instrumentMatchesAsset,
} from "@/lib/portfolio-hierarchy";
import {
  translateExitReason,
  translateRobotStrategyLabel,
  translateRiskProfile,
} from "@/lib/display-text";
import { t } from "@/lib/i18n";
import { formatRiskRewardLabel, formatTargetProfitOrUnavailable } from "@/lib/profit-target";
import { formatCurrency, formatDateTime } from "@/lib/utils";

type DrilldownMode = "open" | "closed";

let competitionCache: CompetitionResponse | null = null;
let competitionPromise: Promise<CompetitionResponse> | null = null;
let tradesCache: Trade[] | null = null;
let tradesPromise: Promise<Trade[]> | null = null;

async function loadCompetitionDetail(): Promise<CompetitionResponse> {
  if (competitionCache) return competitionCache;
  if (!competitionPromise) {
    competitionPromise = api.getCompetition().then((payload) => {
      competitionCache = payload;
      return payload;
    });
  }
  return competitionPromise;
}

async function loadCompetitionTrades(): Promise<Trade[]> {
  if (tradesCache) return tradesCache;
  if (!tradesPromise) {
    tradesPromise = api.getTrades({ portfolio_id: "competition" }).then((rows) => {
      tradesCache = rows;
      return rows;
    });
  }
  return tradesPromise;
}

function directionLabel(direction?: string | null): string {
  const lower = (direction ?? "").toLowerCase();
  if (lower === "long") return t("common.long");
  if (lower === "short") return t("common.short");
  return direction ?? "—";
}

export function AssetTradeDrilldownModal({
  open,
  mode,
  asset,
  onClose,
}: {
  open: boolean;
  mode: DrilldownMode;
  asset: AssetAnalyticsRow | null;
  onClose: () => void;
}) {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [openRows, setOpenRows] = useState<
    NonNullable<CompetitionResponse["open_positions"]>
  >([]);
  const [closedRows, setClosedRows] = useState<Trade[]>([]);

  useEffect(() => {
    if (!open || !asset) return;
    let cancelled = false;
    setLoading(true);
    setError(null);

    const load = async () => {
      try {
        if (mode === "open") {
          const detail = await loadCompetitionDetail();
          const rows = (detail.open_positions ?? []).filter(
            (row) => extractAssetFromPortfolioName(row.portfolio_name) === asset.symbol
          );
          if (!cancelled) setOpenRows(rows);
        } else {
          const trades = await loadCompetitionTrades();
          const rows = trades.filter((row) => instrumentMatchesAsset(row.instrument, asset));
          if (!cancelled) setClosedRows(rows);
        }
      } catch {
        if (!cancelled) setError(t("common.section_unavailable"));
      } finally {
        if (!cancelled) setLoading(false);
      }
    };

    void load();
    return () => {
      cancelled = true;
    };
  }, [open, mode, asset]);

  const title =
    mode === "open"
      ? t("home.open_positions_modal_title", { asset: asset?.symbol ?? "—" })
      : t("home.closed_trades_modal_title", { asset: asset?.symbol ?? "—" });

  return (
    <SimpleModal open={open} title={title} onClose={onClose}>
      {loading ? (
        <p className="text-sm text-muted">{t("common.loading")}</p>
      ) : error ? (
        <p className="text-sm text-muted">{error}</p>
      ) : mode === "open" ? (
        openRows.length ? (
          <div className="space-y-3">
            {openRows.map((row) => (
              <div
                key={row.position_id ?? `${row.portfolio_id}-${row.entry_price}`}
                className="rounded-md border border-border/60 p-3 text-sm"
              >
                <p className="font-medium">{row.portfolio_name}</p>
                <p className="mt-1 text-xs text-muted">
                  {translateRobotStrategyLabel(
                    row.robot_label,
                    undefined,
                    row.strategy_slug
                  )}{" "}
                  · {row.timeframe_he}
                </p>
                {row.risk_name_he || row.risk_slug ? (
                  <p className="mt-1 text-xs text-muted">
                    {t("home.position_risk_tier")}:{" "}
                    {row.risk_name_he ??
                      (row.risk_slug ? translateRiskProfile(row.risk_slug) : "—")}
                  </p>
                ) : null}
                <div className="mt-2 grid gap-2 sm:grid-cols-2">
                  <p>
                    {t("competition.open_direction")}: {directionLabel(row.direction)}
                  </p>
                  <p>
                    {t("competition.entry_price")}: {formatCurrency(row.entry_price)}
                  </p>
                  <p>
                    {t("market.current_price")}: {formatCurrency(row.current_price)}
                  </p>
                  <p>
                    {t("positions.size")}: {row.quantity}
                  </p>
                  <p>
                    {t("home.position_exposure")}:{" "}
                    {row.exposure_usd != null
                      ? formatCurrency(row.exposure_usd)
                      : t("common.metric_unavailable")}
                  </p>
                  <p>
                    {t("competition.stop_loss")}: {formatCurrency(row.stop_loss)}
                  </p>
                  <p>
                    {t("home.position_risk_to_sl")}:{" "}
                    {row.risk_to_sl_usd != null
                      ? formatCurrency(row.risk_to_sl_usd)
                      : t("common.metric_unavailable")}
                  </p>
                  <p>
                    {t("competition.take_profit")}:{" "}
                    {row.take_profit != null ? formatCurrency(row.take_profit) : "—"}
                  </p>
                  <p>
                    {t("home.position_target_profit")}:{" "}
                    {formatTargetProfitOrUnavailable(row.target_profit_usd)}
                  </p>
                  <p>
                    {t("home.position_risk_reward")}:{" "}
                    {formatRiskRewardLabel(row.risk_reward_ratio)}
                  </p>
                  <p className="sm:col-span-2">
                    {t("competition.unrealized_pnl")}:{" "}
                    <PnLDisplay value={row.unrealized_pnl} size="sm" />
                  </p>
                </div>
              </div>
            ))}
          </div>
        ) : (
          <p className="text-sm text-muted">{t("home.open_positions_empty")}</p>
        )
      ) : closedRows.length ? (
        <div className="space-y-3">
          {closedRows.map((row) => (
            <div key={row.id} className="rounded-md border border-border/60 p-3 text-sm">
              <p className="font-medium">{row.instrument}</p>
              <p className="mt-1 text-xs text-muted">{row.strategy_name ?? "—"}</p>
              <div className="mt-2 grid gap-2 sm:grid-cols-2">
                <p>
                  {t("competition.open_direction")}: {directionLabel(row.direction)}
                </p>
                <p>
                  {t("competition.entry_price")}: {formatCurrency(row.entry_price)}
                </p>
                <p>
                  {t("competition.exit_price")}: {formatCurrency(row.exit_price)}
                </p>
                <p>
                  {t("competition.exit_reason")}: {translateExitReason(row.exit_reason)}
                </p>
                <p>
                  {t("competition.realized_pnl")}: <PnLDisplay value={row.pnl} size="sm" />
                </p>
                {row.fees != null ? (
                  <p>
                    {t("journal.fees")}: {formatCurrency(row.fees)}
                  </p>
                ) : null}
                <p className="sm:col-span-2 text-xs text-muted">
                  {t("home.closed_at")}: {formatDateTime(row.close_time)}
                </p>
              </div>
            </div>
          ))}
        </div>
      ) : (
        <p className="text-sm text-muted">{t("home.closed_trades_empty")}</p>
      )}
    </SimpleModal>
  );
}
