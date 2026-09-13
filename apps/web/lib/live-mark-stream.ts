/** Direct Engine SSE for live marks — bypasses Vercel per-tick proxying. */

const LOCAL_ENGINE = "http://localhost:8000";

export type LiveMarkMap = Map<string, { price: number; at: string; source?: string }>;

export function resolveBrowserStreamBaseUrl(): string | null {
  const candidates = [
    process.env.NEXT_PUBLIC_ENGINE_STREAM_URL,
    process.env.NEXT_PUBLIC_ENGINE_URL,
  ]
    .map((value) => value?.trim().replace(/\/$/, "") ?? "")
    .filter(Boolean);

  if (candidates.length > 0) {
    return candidates[0];
  }

  if (typeof window !== "undefined") {
    const host = window.location.hostname;
    if (host === "localhost" || host === "127.0.0.1") {
      return LOCAL_ENGINE;
    }
  }

  return null;
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

export function mergeLiveMarksIntoAssets<T extends { db_symbol: string; latest_price?: number | null }>(
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
    return { ...asset, latest_price: live.price };
  });
}
