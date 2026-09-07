import { NextRequest, NextResponse } from "next/server";
import {
  ENGINE_PROXY_TIMEOUT_MS,
  resolveServerApiKey,
  resolveServerEngineUrl,
} from "@/lib/engine-server";

export const dynamic = "force-dynamic";

async function proxyToEngine(req: NextRequest, pathSegments: string[]) {
  const engineUrl = resolveServerEngineUrl();
  const apiKey = resolveServerApiKey();
  const upstreamPath = pathSegments.join("/");
  const target = `${engineUrl}/api/v1/${upstreamPath}${req.nextUrl.search}`;

  const headers: Record<string, string> = {
    Accept: "application/json",
    ...(apiKey ? { "X-API-Key": apiKey } : {}),
  };

  const init: RequestInit = {
    method: req.method,
    headers,
    cache: "no-store",
    signal: AbortSignal.timeout(ENGINE_PROXY_TIMEOUT_MS),
  };

  if (req.method !== "GET" && req.method !== "HEAD") {
    init.body = await req.text();
    const contentType = req.headers.get("content-type");
    if (contentType) headers["Content-Type"] = contentType;
  }

  try {
    const res = await fetch(target, init);
    const body = await res.text();

    if (!res.ok) {
      return NextResponse.json(
        {
          error: `engine_${res.status}`,
          detail: body.slice(0, 500),
          upstream: upstreamPath,
        },
        { status: res.status }
      );
    }

    return new NextResponse(body, {
      status: res.status,
      headers: {
        "Content-Type": res.headers.get("content-type") ?? "application/json",
      },
    });
  } catch (error) {
    const isTimeout =
      error instanceof Error &&
      (error.name === "TimeoutError" || error.name === "AbortError");

    return NextResponse.json(
      {
        error: isTimeout ? "upstream_timeout" : "upstream_error",
        message: isTimeout
          ? "Engine did not respond in time"
          : error instanceof Error
            ? error.message
            : "Failed to reach engine",
        upstream: upstreamPath,
        engine_url: engineUrl,
      },
      { status: 504 }
    );
  }
}

type RouteContext = { params: { path: string[] } };

export async function GET(req: NextRequest, ctx: RouteContext) {
  return proxyToEngine(req, ctx.params.path ?? []);
}

export async function POST(req: NextRequest, ctx: RouteContext) {
  return proxyToEngine(req, ctx.params.path ?? []);
}

export async function PUT(req: NextRequest, ctx: RouteContext) {
  return proxyToEngine(req, ctx.params.path ?? []);
}

export async function PATCH(req: NextRequest, ctx: RouteContext) {
  return proxyToEngine(req, ctx.params.path ?? []);
}

export async function DELETE(req: NextRequest, ctx: RouteContext) {
  return proxyToEngine(req, ctx.params.path ?? []);
}
