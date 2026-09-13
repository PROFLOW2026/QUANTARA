import { NextResponse } from "next/server";

import { getEngineDnsStats, prefetchEngineDns } from "@/lib/engine-dns";
import { resolveServerEngineUrl } from "@/lib/engine-server";

export const dynamic = "force-dynamic";

/** Read-only resolver counters for production durability verification. */
export async function GET() {
  try {
    const hostname = new URL(resolveServerEngineUrl()).hostname;
    await prefetchEngineDns(hostname);
  } catch {
    // Stats still report resolver attempts/failures from prefetch.
  }
  return NextResponse.json(getEngineDnsStats());
}
