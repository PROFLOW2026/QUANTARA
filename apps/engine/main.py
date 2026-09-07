"""QUANTARA Engine FastAPI entry point."""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from quantara_engine.api.routes import router
from quantara_engine.db.session import init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="QUANTARA Engine", version="0.1.0", lifespan=lifespan)
app.include_router(router)


if __name__ == "__main__":
    import uvicorn

    from quantara_engine.core.config import settings

    uvicorn.run(
        "main:app",
        host=settings.engine_host,
        port=settings.engine_port,
        reload=True,
    )
