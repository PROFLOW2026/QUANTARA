import crypto from "crypto";
import { NextResponse } from "next/server";

import { resolveServerApiKey } from "@/lib/engine-server";

export const dynamic = "force-dynamic";

const STREAM_TOKEN_SCOPE = "live-marks";
const STREAM_TOKEN_TTL_SEC = 3600;

function issueStreamToken(): string {
  const key = resolveServerApiKey();
  const exp = Math.floor(Date.now() / 1000) + STREAM_TOKEN_TTL_SEC;
  const payload = `${STREAM_TOKEN_SCOPE}:${exp}`;
  const sig = crypto.createHmac("sha256", key).update(payload).digest("hex");
  return `${exp}.${sig}`;
}

/** Short-lived token for direct Engine SSE (one Vercel call per browser session). */
export async function GET() {
  return NextResponse.json({ token: issueStreamToken(), expires_in: STREAM_TOKEN_TTL_SEC });
}
