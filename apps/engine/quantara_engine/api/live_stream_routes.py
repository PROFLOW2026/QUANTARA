"""SSE live mark stream — direct Engine delivery (bypasses Vercel per tick)."""

from __future__ import annotations

import asyncio
import json
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse

from quantara_engine.api.cors import cors_response_headers, is_allowed_origin
from quantara_engine.api.deps import verify_api_key
from quantara_engine.market_data.streaming.hub import get_live_mark_hub
from quantara_engine.market_data.streaming.stream_manager import stream_manager_status
from quantara_engine.market_data.streaming.stream_token import verify_stream_token

live_stream_router = APIRouter(prefix="/api/v1/market-data", tags=["market-data-stream"])

SSE_HEARTBEAT_SEC = 5.0


def verify_stream_access(
    request: Request,
    x_api_key: Annotated[str | None, Header()] = None,
    token: Annotated[str | None, Query()] = None,
) -> None:
    if request.method == "OPTIONS":
        return
    if x_api_key:
        try:
            verify_api_key(request, x_api_key)
            return
        except HTTPException:
            pass
    if token and verify_stream_token(token):
        return
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or missing stream credentials",
    )


@live_stream_router.get("/live-stream")
async def live_mark_sse(
    request: Request,
    _: Annotated[None, Depends(verify_stream_access)],
):
    hub = get_live_mark_hub()
    loop = asyncio.get_running_loop()
    queue = hub.subscribe(loop)
    if queue is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Too many live stream subscribers",
        )
    origin = request.headers.get("origin")

    async def event_generator():
        try:
            yield f"data: {json.dumps(hub.snapshot())}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    payload = await asyncio.wait_for(queue.get(), timeout=SSE_HEARTBEAT_SEC)
                    if await request.is_disconnected():
                        break
                    yield f"data: {json.dumps(payload)}\n\n"
                except asyncio.TimeoutError:
                    if await request.is_disconnected():
                        break
                    yield ": heartbeat\n\n"
        finally:
            hub.unsubscribe(queue)

    headers = {
        "Cache-Control": "no-cache, no-transform",
        "X-Accel-Buffering": "no",
    }
    if origin and is_allowed_origin(origin):
        headers.update(cors_response_headers(origin))

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers=headers,
    )


@live_stream_router.get("/live-stream/status")
def live_stream_status(
    _: Annotated[None, Depends(verify_stream_access)],
):
    return stream_manager_status()
