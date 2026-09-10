"use client";

import { useCallback, useEffect, useState } from "react";
import { api, ApiError, type TradingControlState, type TradingControlStatus } from "@/lib/api-client";

const STATE_LABELS: Record<TradingControlState, string> = {
  running: "פעיל — מסחר רגיל",
  pause_new_entries: "עצור עסקאות חדשות",
  pause_trading: "עצור מסחר",
  flattening: "סוגר כל הפוזיציות…",
  stopped: "מושהה — כל הפוזיציות סגורות",
};

export function OwnerTradingControls() {
  const [status, setStatus] = useState<TradingControlStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [confirmFlatten, setConfirmFlatten] = useState(false);

  const load = useCallback(async () => {
    try {
      setStatus(await api.getTradingControl());
      setError(null);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "שגיאה בטעינת מצב מסחר");
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  async function act(action: string) {
    setBusy(true);
    try {
      setStatus(await api.setTradingControl(action));
      setConfirmFlatten(false);
      setError(null);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "פעולה נכשלה");
    } finally {
      setBusy(false);
    }
  }

  const state = status?.state ?? "running";

  return (
    <div className="space-y-3">
      <p className="text-sm">
        מצב נוכחי: <strong>{STATE_LABELS[state] ?? state}</strong>
      </p>
      {status?.open_positions_remaining != null && (
        <p className="text-xs text-muted">
          פוזיציות פתוחות: {status.open_positions_remaining}
          {state === "flattening" && status.flatten_progress_pct != null
            ? ` · התקדמות: ${status.flatten_progress_pct}%`
            : null}
          {state === "flattening" && status.flatten_started_at
            ? ` · התחיל: ${new Date(status.flatten_started_at).toLocaleString("he-IL")}`
            : null}
        </p>
      )}
      {status?.assets_awaiting_market_reopen && status.assets_awaiting_market_reopen.length > 0 && (
        <p className="text-xs text-muted">
          ממתינים לפתיחת שוק: {status.assets_awaiting_market_reopen.join(", ")}
        </p>
      )}
      {error && <p className="text-sm text-loss">{error}</p>}
      <div className="flex flex-wrap gap-2">
        <button
          type="button"
          disabled={busy || state === "running"}
          onClick={() => act("resume_trading")}
          className="rounded-md bg-profit px-3 py-2 text-sm text-white disabled:opacity-40"
        >
          הפעל מסחר
        </button>
        <button
          type="button"
          disabled={busy || state === "pause_new_entries"}
          onClick={() => act("pause_new_entries")}
          className="rounded-md bg-amber-600 px-3 py-2 text-sm text-white disabled:opacity-40"
        >
          עצור עסקאות חדשות
        </button>
        <button
          type="button"
          disabled={busy || state === "pause_trading"}
          onClick={() => act("pause_trading")}
          className="rounded-md bg-orange-700 px-3 py-2 text-sm text-white disabled:opacity-40"
        >
          עצור מסחר
        </button>
        {!confirmFlatten ? (
          <button
            type="button"
            disabled={busy || state === "flattening"}
            onClick={() => setConfirmFlatten(true)}
            className="rounded-md bg-loss px-3 py-2 text-sm text-white disabled:opacity-40"
          >
            עצור וסגור הכל
          </button>
        ) : (
          <button
            type="button"
            disabled={busy}
            onClick={() => act("flatten_all")}
            className="rounded-md bg-loss px-3 py-2 text-sm font-bold text-white"
          >
            אשר — סגור הכל
          </button>
        )}
      </div>
    </div>
  );
}
