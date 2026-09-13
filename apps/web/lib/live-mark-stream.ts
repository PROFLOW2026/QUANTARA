/** Direct Engine SSE for live marks — bypasses Vercel per-tick proxying. */

import type { AssetAnalyticsSummary } from "@/lib/api-client";

const LOCAL_ENGINE = "http://localhost:8000";

export type LiveMarkMap = Map<string, { price: number; at: string; source?: string }>;

function isLocalEngineUrl(url: string): boolean {
  return /localhost|127\.0\.0\.1/.test(url);
}

/** Local-dev fallback only — production uses /api/engine/stream-base at runtime. */
export function resolveBrowserStreamBaseUrl(): string | null {
  const publicUrl = process.env.NEXT_PUBLIC_ENGINE_URL?.trim().replace(/\/$/, "") ?? "";
  if (publicUrl && isLocalEngineUrl(publicUrl)) {
    return publicUrl;
  }

  if (typeof window !== "undefined") {
    const host = window.location.hostname;
    if (host === "localhost" || host === "127.0.0.1") {
      return LOCAL_ENGINE;
    }
  }

  return null;
}

/** Prefer server ENGINE_URL at runtime so Quick Tunnel changes do not require rebuild. */
export async function resolveRuntimeStreamBaseUrl(): Promise<string | null> {
  if (typeof window !== "undefined") {
    try {
      const res = await fetch("/api/engine/stream-base", { cache: "no-store" });
      if (res.ok) {
        const payload = (await res.json()) as { baseUrl?: string };
        const baseUrl = payload.baseUrl?.trim().replace(/\/$/, "") ?? "";
        if (baseUrl) {
          return baseUrl;
        }
      }
    } catch {
      // fall through to build-time / localhost defaults
    }
  }
  return resolveBrowserStreamBaseUrl();
}

export function buildLiveStreamUrl(baseUrl: string, token: string): string {
  const url = new URL(`${baseUrl}/api/v1/market-data/live-stream`);
  url.searchParams.set("token", token);
  return url.toString();
}

export function parseLiveMarkEvent(raw: string): LiveMarkMap | null {
  try {
    const payload = JSON.parse(raw) as {
      type?: string;
      marks?: Record<
        string,
        { db_symbol?: string; price?: number; at?: string; source?: string }
      >;
    };
    if (payload.type !== "marks" || !payload.marks) {
      return null;
    }
    const out: LiveMarkMap = new Map();
    for (const entry of Object.values(payload.marks)) {
      const sym = entry.db_symbol;
      if (!sym || entry.price == null || !Number.isFinite(entry.price)) {
        continue;
      }
      out.set(sym, {
        price: entry.price,
        at: entry.at ?? "",
        source: entry.source,
      });
    }
    return out;
  } catch {
    return null;
  }
}

type LiveValuationAsset = {
  db_symbol: string;
  latest_price?: number | null;
  unrealized_pnl?: number;
  realized_pnl?: number;
  total_pnl?: number;
  open_exposure?: number | null;
  open_positions?: number;
};

function adjustLiveValuation<T extends LiveValuationAsset>(asset: T, livePrice: number): T {
  const restMark = asset.latest_price;
  const next: T = { ...asset, latest_price: livePrice };

  if (
    restMark == null ||
    restMark <= 0 ||
    !asset.open_positions ||
    asset.open_positions <= 0 ||
    Math.abs(livePrice - restMark) < 1e-9
  ) {
    return next;
  }

  if (asset.open_exposure != null) {
    next.open_exposure = Math.round(asset.open_exposure * (livePrice / restMark) * 100) / 100;
  }

  if (asset.unrealized_pnl != null && asset.open_exposure != null && asset.open_exposure > 0) {
    const qtyApprox = asset.open_exposure / restMark;
    const delta = (livePrice - restMark) * qtyApprox;
    next.unrealized_pnl = Math.round((asset.unrealized_pnl + delta) * 100) / 100;
    const realized = asset.realized_pnl ?? 0;
    next.total_pnl = Math.round((realized + next.unrealized_pnl) * 100) / 100;
  }

  return next;
}

export function mergeLiveMarksIntoAssets<T extends LiveValuationAsset>(
  assets: T[],
  liveMarks: LiveMarkMap
): T[] {
  if (!liveMarks.size) {
    return assets;
  }
  return assets.map((asset) => {
    const live = liveMarks.get(asset.db_symbol);
    if (!live) {
      return asset;
    }
    return adjustLiveValuation(asset, live.price);
  });
}

export function applyLiveMarksToExposureSummary(
  summary: AssetAnalyticsSummary | null | undefined,
  assetsBefore: LiveValuationAsset[],
  assetsAfter: LiveValuationAsset[]
): AssetAnalyticsSummary | null | undefined {
  if (!summary) {
    return summary;
  }

  let unrealizedDelta = 0;
  let exposureDelta = 0;
  for (let i = 0; i < assetsBefore.length; i += 1) {
    const before = assetsBefore[i];
    const after = assetsAfter[i];
    if (!before?.open_positions || before.open_positions <= 0) {
      continue;
    }
    if (before.unrealized_pnl != null && after.unrealized_pnl != null) {
      unrealizedDelta += after.unrealized_pnl - before.unrealized_pnl;
    }
    if (before.open_exposure != null && after.open_exposure != null) {
      exposureDelta += after.open_exposure - before.open_exposure;
    }
  }

  if (Math.abs(unrealizedDelta) < 1e-9 && Math.abs(exposureDelta) < 1e-9) {
    return summary;
  }

  const openExposure =
    summary.open_exposure != null
      ? Math.round((summary.open_exposure + exposureDelta) * 100) / 100
      : summary.open_exposure;

  return {
    ...summary,
    open_exposure: openExposure,
    total_equity: Math.round((summary.total_equity + unrealizedDelta) * 100) / 100,
  };
}
