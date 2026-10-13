"""FastAPI dependencies shared by the v1 routers.

The application stores a :class:`Database` instance on
``app.state.db`` during the lifespan startup. These helpers just unwrap
that container into the specific resources each route needs.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated, cast

import asyncpg
from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from xspeed_server.app.config import Settings, get_settings
from xspeed_server.app.db import Database

__all__ = [
    "DatabaseDep",
    "PoolDep",
    "SessionDep",
    "SettingsDep",
    "get_settings_dep",
    "get_db",
    "get_pool",
    "get_session",
]


def _state_db(request: Request) -> Database:
    return cast(Database, request.app.state.db)


def _get_db(request: Request) -> Database:
    return _state_db(request)


def _get_pool(request: Request) -> asyncpg.Pool:
    return _state_db(request).pool


async def _get_session(request: Request) -> AsyncIterator[AsyncSession]:
    async for session in _state_db(request).session():
        yield session


SettingsDep = Annotated[Settings, Depends(get_settings)]
DatabaseDep = Annotated[Database, Depends(_get_db)]
PoolDep = Annotated[asyncpg.Pool, Depends(_get_pool)]
SessionDep = Annotated[AsyncSession, Depends(_get_session)]


def get_settings_dep() -> Settings:  # pragma: no cover - alias kept for tests
    return get_settings()


def get_db(request: Request) -> Database:  # pragma: no cover - alias kept for tests
    return _state_db(request)


def get_pool(request: Request) -> asyncpg.Pool:  # pragma: no cover - alias
    return _state_db(request).pool


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:  # pragma: no cover - alias
    async for session in _state_db(request).session():
        yield session
