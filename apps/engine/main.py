"""QUANTARA Engine FastAPI entry point."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from quantara_engine.api.cors import cors_response_headers, is_allowed_origin, resolve_cors_origins
from quantara_engine.api.routes import router
from quantara_engine.core.config import settings
from quantara_engine.db.session import init_db


class QuantaCORSMiddleware(BaseHTTPMiddleware):
    """Apply CORS using fresh origin resolution (env + repo .env) per request."""

    async def dispatch(self, request: Request, call_next):
        origin = request.headers.get("origin")

        if request.method == "OPTIONS" and request.headers.get("access-control-request-method"):
            if is_allowed_origin(origin):
                return Response(status_code=204, headers=cors_response_headers(origin))
            return Response(status_code=400, content="CORS origin not allowed")

        try:
            response = await call_next(request)
        except Exception:
            response = Response(status_code=500, content="Internal Server Error")

        if is_allowed_origin(origin):
            for key, value in cors_response_headers(origin).items():
                response.headers[key] = value

        return response


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    origins = resolve_cors_origins()
    print(f"QUANTARA CORS origins: {', '.join(origins)}")
    yield


app = FastAPI(title="QUANTARA Engine", version="0.1.0", lifespan=lifespan)
app.add_middleware(QuantaCORSMiddleware)  # CORS on success and error responses
app.include_router(router)


if __name__ == "__main__":
    import os

    import uvicorn

    # Hot reload is dev-only. START_QUANTARA uses uvicorn CLI without --reload.
    reload = os.environ.get("QUANTARA_DEV_RELOAD", "").strip().lower() in ("1", "true", "yes")
    uvicorn.run(
        "main:app",
        host=settings.engine_host,
        port=settings.engine_port,
        reload=reload,
    )
