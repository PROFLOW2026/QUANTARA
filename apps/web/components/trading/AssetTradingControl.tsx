"use client";

import { useState } from "react";
import { Badge } from "@/components/ui/badge";
import { SimpleModal } from "@/components/ui/SimpleModal";
import { api, ApiError } from "@/lib/api-client";
import { t } from "@/lib/i18n";

type CloseStatus = "idle" | "submitting" | "filled" | "pending" | "rejected" | "error";

function translateCloseStatus(status: CloseStatus): string {
  switch (status) {
    case "submitting":
      return t("home.manual_close_submitted");
    case "filled":
      return t("home.manual_close_filled");
    case "pending":
      return t("home.manual_close_pending");
    case "rejected":
      return t("home.manual_close_rejected");
    case "error":
      return t("home.manual_close_failed");
    default:
      return t("home.close_position");
  }
}

export function AssetTradingStateBadge({ paused }: { paused?: boolean }) {
  return (
    <Badge variant={paused ? "warning" : "success"}>
      {paused ? t("home.trading_paused") : t("home.trading_active")}
    </Badge>
  );
}

export function AssetTradingPauseControl({
  dbSymbol,
  displaySymbol,
  paused,
  onChanged,
}: {
  dbSymbol: string;
  displaySymbol: string;
  paused?: boolean;
  onChanged?: () => void;
}) {
  const [confirmPause, setConfirmPause] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function togglePause() {
    setBusy(true);
    setError(null);
    try {
      if (paused) {
        await api.setAssetTradingControl("research", dbSymbol, "resume");
      } else {
        await api.setAssetTradingControl("research", dbSymbol, "pause");
      }
      setConfirmPause(false);
      onChanged?.();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : t("common.section_unavailable"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <button
        type="button"
        disabled={busy}
        onClick={() => (paused ? void togglePause() : setConfirmPause(true))}
        className="inline-flex items-center"
        aria-label={
          paused
            ? t("home.resume_trading_aria", { asset: displaySymbol })
            : t("home.pause_trading_aria", { asset: displaySymbol })
        }
      >
        <AssetTradingStateBadge paused={paused} />
      </button>

      <SimpleModal
        open={confirmPause}
        title={t("home.pause_trading_confirm_title", { asset: displaySymbol })}
        onClose={() => setConfirmPause(false)}
      >
        <p className="text-sm leading-relaxed">{t("home.pause_trading_confirm_body")}</p>
        {error ? <p className="mt-3 text-sm text-loss">{error}</p> : null}
        <div className="mt-4 flex flex-wrap justify-end gap-2">
          <button
            type="button"
            className="rounded-md border border-border px-3 py-2 text-sm"
            onClick={() => setConfirmPause(false)}
          >
            {t("common.cancel")}
          </button>
          <button
            type="button"
            disabled={busy}
            className="rounded-md bg-warning px-3 py-2 text-sm text-white disabled:opacity-50"
            onClick={() => void togglePause()}
          >
            {t("home.pause_trading_confirm_action")}
          </button>
        </div>
      </SimpleModal>
    </>
  );
}

export function CloseAllPositionsButton({
  dbSymbol,
  displaySymbol,
  openCount,
  exposure,
  unrealizedPnl,
  slRisk,
  onClosed,
}: {
  dbSymbol: string;
  displaySymbol: string;
  openCount: number;
  exposure?: number | null;
  unrealizedPnl?: number;
  slRisk?: number | null;
  onClosed?: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState<CloseStatus>("idle");
  const [error, setError] = useState<string | null>(null);

  if (openCount <= 0) return null;

  async function closeAll() {
    setBusy(true);
    setStatus("submitting");
    setError(null);
    try {
      const result = await api.closeAllAssetPositions(dbSymbol, "research");
      if (result.rejected > 0) {
        setStatus("rejected");
      } else if (result.pending_market > 0) {
        setStatus("pending");
      } else {
        setStatus("filled");
      }
      setOpen(false);
      onClosed?.();
    } catch (e) {
      setStatus("error");
      setError(e instanceof ApiError ? e.message : t("common.section_unavailable"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <button
        type="button"
        disabled={busy}
        onClick={() => setOpen(true)}
        className="rounded-md border border-loss/40 px-2.5 py-1 text-xs text-loss hover:bg-loss/10 disabled:opacity-50"
      >
        {status === "idle" ? t("home.close_all_positions") : translateCloseStatus(status)}
      </button>

      <SimpleModal
        open={open}
        title={t("home.close_all_confirm_title", { asset: displaySymbol })}
        onClose={() => setOpen(false)}
      >
        <div className="space-y-2 text-sm">
          <p>{t("home.close_all_confirm_positions", { count: openCount })}</p>
          {exposure != null ? (
            <p>{t("home.close_all_confirm_exposure", { value: exposure.toFixed(2) })}</p>
          ) : null}
          <p>
            {t("home.close_all_confirm_pnl", {
              value: (unrealizedPnl ?? 0).toFixed(2),
            })}
          </p>
          {slRisk != null ? (
            <p>{t("home.close_all_confirm_sl_risk", { value: slRisk.toFixed(2) })}</p>
          ) : null}
          <p className="text-muted">{t("home.close_all_confirm_disclaimer")}</p>
        </div>
        {error ? <p className="mt-3 text-sm text-loss">{error}</p> : null}
        <div className="mt-4 flex flex-wrap justify-end gap-2">
          <button
            type="button"
            className="rounded-md border border-border px-3 py-2 text-sm"
            onClick={() => setOpen(false)}
          >
            {t("common.cancel")}
          </button>
          <button
            type="button"
            disabled={busy}
            className="rounded-md bg-loss px-3 py-2 text-sm text-white disabled:opacity-50"
            onClick={() => void closeAll()}
          >
            {t("home.close_all_confirm_action")}
          </button>
        </div>
      </SimpleModal>
    </>
  );
}

export function ClosePositionButton({
  positionId,
  onClosed,
}: {
  positionId: string;
  onClosed?: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState<CloseStatus>("idle");

  async function closeOne() {
    if (busy || status === "filled") return;
    setBusy(true);
    setStatus("submitting");
    try {
      const result = await api.closePosition(positionId, "research");
      if (result.status === "filled" || result.status === "already_closed") {
        setStatus("filled");
        onClosed?.();
      } else if (result.status === "pending_market") {
        setStatus("pending");
      } else {
        setStatus("rejected");
      }
    } catch {
      setStatus("error");
    } finally {
      setBusy(false);
    }
  }

  return (
    <button
      type="button"
      disabled={busy || status === "filled"}
      onClick={() => void closeOne()}
      className="rounded-md border border-loss/40 px-2.5 py-1 text-xs text-loss hover:bg-loss/10 disabled:opacity-50"
    >
      {translateCloseStatus(status)}
    </button>
  );
}
