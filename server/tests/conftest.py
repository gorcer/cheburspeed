"""Shared pytest fixtures for the XSpeed server test suite.

The tests run against a real PostgreSQL instance (the project standard
for JSONB / ``percentile_cont``). The ``TEST_DATABASE_URL`` environment
variable is required; when missing the test session is skipped with a
clear message instead of failing.

A clean schema is enforced per-test by truncating the four task
related tables inside the ``clean_db`` fixture. The slowapi
:class:`Limiter` storage is also reset between tests so the rate
limit assertions stay deterministic.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from typing import Any

import asyncpg
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

os.environ.setdefault(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://xspeed:xspeed@localhost:5433/xspeed_test",
)


def _test_database_url() -> str:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL is not configured")
    return url


def _asyncpg_dsn(database_url: str) -> str:
    if database_url.startswith("postgresql+asyncpg://"):
        return "postgresql://" + database_url[len("postgresql+asyncpg://") :]
    if database_url.startswith("postgresql://"):
        return database_url
    raise ValueError(f"Unsupported TEST_DATABASE_URL scheme: {database_url!r}")


@pytest.fixture(scope="session")
def test_database_url() -> str:
    return _test_database_url()


@pytest_asyncio.fixture
async def app_env(monkeypatch: pytest.MonkeyPatch, test_database_url: str) -> dict[str, Any]:
    """Configure environment variables and reset module-level caches."""
    monkeypatch.setenv("DATABASE_URL", test_database_url)
    monkeypatch.setenv("JWT_SECRET", "test-secret-test-secret-test-secret")
    monkeypatch.setenv("LOG_LEVEL", "WARNING")

    from xspeed_server import config as config_module

    config_module.get_settings.cache_clear()

    from xspeed_server.app.core import ratelimit

    ratelimit.limiter.reset()

    return {"test_database_url": test_database_url}


@pytest_asyncio.fixture
async def clean_db(app_env: dict[str, Any]) -> AsyncIterator[None]:
    """Truncate the task-related tables before each test."""
    dsn = _asyncpg_dsn(app_env["test_database_url"])
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            """
            TRUNCATE TABLE
                task_results,
                tasks,
                task_results_archive,
                probes,
                destinations,
                regions,
                admins
            RESTART IDENTITY CASCADE
            """
        )
    finally:
        await conn.close()
    yield


class _LifespanManager:
    """Run the FastAPI lifespan manually so ``app.state.db`` is populated."""

    def __init__(self, app: Any) -> None:
        self._app = app
        self._cm: Any | None = None

    async def __aenter__(self) -> None:
        self._cm = self._app.router.lifespan_context(self._app)
        await self._cm.__aenter__()

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        if self._cm is not None:
            await self._cm.__aexit__(exc_type, exc, tb)
            self._cm = None


@pytest_asyncio.fixture
async def app(clean_db: None) -> AsyncIterator[Any]:
    """Build a FastAPI app instance with the real lifespan."""
    from xspeed_server.app.main import create_app

    application = create_app()
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://test"
    ) as client:
        async with _LifespanManager(application):
            yield application, client


@pytest_asyncio.fixture
async def client(app: Any) -> AsyncIterator[AsyncClient]:
    """HTTP test client bound to the live FastAPI app."""
    _, http_client = app
    yield http_client


@pytest_asyncio.fixture
async def app_instance(app: Any) -> Any:
    """Raw FastAPI app instance (for direct access to ``app.state``)."""
    application, _ = app
    return application


__all__ = [
    "app_env",
    "app_instance",
    "app",
    "clean_db",
    "client",
    "test_database_url",
]
