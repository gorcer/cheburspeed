"""FastAPI application factory and lifespan management.

The factory wires together:

* the slowapi :class:`Limiter` (mounted both as middleware and on
  ``app.state.limiter``),
* a single :class:`xspeed_server.app.db.Database` instance exposing
  the asyncpg pool and the SQLAlchemy async engine,
* the v1 HTTP routers (``/api/v1/tasks``, ``/api/v1/regions``,
  ``/api/v1/probes``),
* a placeholder :class:`Hub` for the WebSocket task (DAT-325.4),
* the APScheduler that will run the archive job (DAT-325.7).

The lifespan is the single place that touches the database: routes only
read the pool from ``app.state.db``.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from xspeed_server.app.api.v1 import probes, regions, tasks
from xspeed_server.app.config import get_settings
from xspeed_server.app.core.ratelimit import limiter
from xspeed_server.app.db import Database
from xspeed_server.app.tasks import start_archive_scheduler
from xspeed_server.app.ws import Hub

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from apscheduler.schedulers.asyncio import AsyncIOScheduler

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    logging.basicConfig(level=settings.log_level, force=True)
    logger.info("xspeed-server starting up")

    db = await Database.create(settings)
    async with db.pool.acquire() as conn:
        await conn.execute("SELECT 1")
    logger.info("database connection verified")
    app.state.db = db
    app.state.hub = Hub()
    scheduler: AsyncIOScheduler | None = start_archive_scheduler(db)
    app.state.scheduler = scheduler

    try:
        yield
    finally:
        logger.info("xspeed-server shutting down")
        if scheduler is not None:
            scheduler.shutdown(wait=False)
        await db.dispose()


def _configure_cors(app: FastAPI) -> None:
    settings = get_settings()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.frontend_origin],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )


def create_app() -> FastAPI:
    """Build and return a fully-configured FastAPI application."""
    app = FastAPI(
        title="XSpeed API",
        version="0.1.0",
        description="HTTP API for the XSpeed vless speedtest service.",
        lifespan=lifespan,
    )
    _configure_cors(app)

    app.state.limiter = limiter
    # slowapi exposes a sync handler; FastAPI accepts both sync and async
    # exception handlers but the slowapi signature uses the older Request
    # generic that doesn't line up with the current starlette types — cast.
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)  # type: ignore[arg-type]
    app.add_middleware(SlowAPIMiddleware)

    app.include_router(tasks.router, prefix="/api/v1")
    app.include_router(regions.router, prefix="/api/v1")
    app.include_router(probes.router, prefix="/api/v1")

    @app.get("/healthz", tags=["meta"])
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/", tags=["meta"])
    async def root() -> dict[str, Any]:
        return {
            "service": "xspeed-server",
            "version": app.version,
            "docs": "/docs",
        }

    return app


__all__ = ["create_app", "lifespan"]
