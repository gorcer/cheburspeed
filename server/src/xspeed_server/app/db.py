"""Database access layer for XSpeed.

Exposes a single :class:`Database` container that owns the
:class:`asyncpg.Pool` and the SQLAlchemy :class:`AsyncEngine`. The
container is built once during the FastAPI lifespan and shared via
:mod:`xspeed_server.app.deps`.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass

import asyncpg
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from xspeed_server.config import Settings
from xspeed_server.models import Base

logger = logging.getLogger(__name__)

__all__ = ["Database", "get_base_metadata"]


def _asyncpg_dsn(database_url: str) -> str:
    """Convert a SQLAlchemy URL (``postgresql+asyncpg://…``) into an asyncpg DSN."""
    if database_url.startswith("postgresql+asyncpg://"):
        return "postgresql://" + database_url[len("postgresql+asyncpg://") :]
    if database_url.startswith("postgresql://"):
        return database_url
    raise ValueError(f"Unsupported database scheme in DATABASE_URL: {database_url!r}")


@dataclass(slots=True)
class Database:
    """Container holding the asyncpg pool and the SQLAlchemy async engine."""

    pool: asyncpg.Pool
    engine: AsyncEngine
    session_factory: async_sessionmaker[AsyncSession]

    @classmethod
    async def create(cls, settings: Settings) -> Database:
        """Create the pool, the engine, and a session factory bound to ``settings``."""
        dsn = _asyncpg_dsn(settings.database_url)
        pool = await asyncpg.create_pool(
            dsn=dsn,
            min_size=settings.db_pool_min_size,
            max_size=settings.db_pool_max_size,
            command_timeout=settings.db_pool_command_timeout,
        )
        from sqlalchemy.ext.asyncio import async_engine_from_config

        engine = async_engine_from_config(
            {"sqlalchemy.url": settings.database_url},
            prefix="sqlalchemy.",
        )
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        logger.info(
            "database pool created (min=%d, max=%d)",
            settings.db_pool_min_size,
            settings.db_pool_max_size,
        )
        return cls(pool=pool, engine=engine, session_factory=session_factory)

    async def dispose(self) -> None:
        """Close the pool and the engine."""
        await self.engine.dispose()
        await self.pool.close()
        logger.info("database pool disposed")

    async def session(self) -> AsyncIterator[AsyncSession]:
        """Yield a SQLAlchemy async session tied to the request lifecycle."""
        async with self.session_factory() as session:
            yield session


def get_base_metadata() -> type[Base]:
    """Expose the SQLAlchemy ``Base`` for tooling (alembic, scripts)."""
    return Base
