import { NextResponse } from "next/server";

const ENGINE_URL =
  process.env.NEXT_PUBLIC_ENGINE_URL ?? "http://localhost:8000";
const API_KEY =
  process.env.NEXT_PUBLIC_API_KEY ??
  process.env.QUANTARA_API_KEY ??
  "";

export const dynamic = "force-dynamic";

export async function GET() {
  const url = `${ENGINE_URL.replace(/\/$/, "")}/api/v1/competition`;

  try {
    const res = await fetch(url, {
      headers: {
        Accept: "application/json",
        ...(API_KEY ? { "X-API-Key": API_KEY } : {}),
      },
      cache: "no-store",
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
    return NextResponse.json({ error: message }, { status: 502 });
  }
}
