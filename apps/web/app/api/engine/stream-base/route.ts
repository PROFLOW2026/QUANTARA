import { NextResponse } from "next/server";

import { resolveServerEngineUrl } from "@/lib/engine-server";

export const dynamic = "force-dynamic";

/** Runtime Engine base URL for direct browser SSE (uses ENGINE_URL — no rebuild on tunnel change). */
export async function GET() {
  try {
    const baseUrl = resolveServerEngineUrl();
    return NextResponse.json({ baseUrl });
  } catch (error) {
    return NextResponse.json(
      {
        error: "engine_url_not_configured",
        message: error instanceof Error ? error.message : "ENGINE_URL not configured",
      },
      { status: 503 }
    );
  }
}
