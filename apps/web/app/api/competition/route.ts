import { NextResponse } from "next/server";

const LOCAL_ENGINE = "http://localhost:8000";
const LIVE_TUNNEL_FALLBACK =
  "https://afternoon-details-occasional-undergraduate.trycloudflare.com";
const FETCH_TIMEOUT_MS = 15_000;

function resolveEngineUrl(): string {
  const candidates = [
    process.env.ENGINE_URL,
    process.env.STABLE_ENGINE_URL,
    process.env.QUANTARA_ENGINE_TUNNEL_URL,
    process.env.NEXT_PUBLIC_ENGINE_URL,
  ]
    .map((value) => value?.trim().replace(/\/$/, "") ?? "")
    .filter(Boolean);

  const onVercel = process.env.VERCEL === "1";
  for (const url of candidates) {
    if (onVercel && /localhost|127\.0\.0\.1/.test(url)) continue;
    return url;
  }

  if (onVercel) {
    return (
      process.env.QUANTARA_ENGINE_TUNNEL_URL ?? LIVE_TUNNEL_FALLBACK
    ).replace(/\/$/, "");
  }

  return LOCAL_ENGINE;
}

function resolveApiKey(): string {
  return (
    process.env.QUANTARA_API_KEY ??
    process.env.NEXT_PUBLIC_API_KEY ??
    ""
  );
}

export const dynamic = "force-dynamic";

export async function GET() {
  const engineUrl = resolveEngineUrl();
  const url = `${engineUrl}/api/v1/competition`;
  const apiKey = resolveApiKey();

  try {
    const res = await fetch(url, {
      headers: {
        Accept: "application/json",
        ...(apiKey ? { "X-API-Key": apiKey } : {}),
      },
      cache: "no-store",
      signal: AbortSignal.timeout(FETCH_TIMEOUT_MS),
    });

    const body = await res.text();
    if (!res.ok) {
      return NextResponse.json(
        { error: `Engine API ${res.status}`, detail: body.slice(0, 200) },
        { status: res.status }
      );
    }

    return new NextResponse(body, {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  } catch (error) {
    const message =
      error instanceof Error ? error.message : "Failed to reach engine";
    return NextResponse.json(
      { error: message, engine_url: engineUrl },
      { status: 502 }
    );
  }
}
