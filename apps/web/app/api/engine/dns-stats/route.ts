import { NextResponse } from "next/server";

import { getEngineDnsStats } from "@/lib/engine-dns";

export const dynamic = "force-dynamic";

/** Read-only resolver counters for production durability verification. */
export async function GET() {
  return NextResponse.json(getEngineDnsStats());
}
