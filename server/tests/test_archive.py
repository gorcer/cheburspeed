"""Tests for the ``task_results`` cold-storage archiver (DAT-325.7).

The mover is exercised through the live FastAPI lifespan so the
``app.state.db`` :class:`Database` is the same instance the
APScheduler job would call from production. The tests rely on the
shared ``clean_db`` fixture to start from an empty schema and use raw
asyncpg to seed ``regions``, ``probes``, ``tasks`` and backdated
``task_results`` rows.

The behaviours covered here are the ones called out in the task spec:

* rows with ``created_at`` older than one day are deleted from
  ``task_results`` and inserted into ``task_results_archive`` with
  their ``archived_at`` stamp populated;
* fresh rows are left untouched and the archived row count is
  preserved (``task_results + task_results_archive`` is constant);
* the helper used by the future ``/admin/stats`` endpoint reports
  ``None`` before the first run and a positive float afterwards.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg
import pytest

from xspeed_server.app.tasks.archive import (
    archive_lag_seconds,
    archive_old_results,
)

DATABASE_URL_ENV = "TEST_DATABASE_URL"


def _test_dsn() -> str:
    url = os.environ[DATABASE_URL_ENV]
    if url.startswith("postgresql+asyncpg://"):
        return "postgresql://" + url[len("postgresql+asyncpg://") :]
    return url


async def _seed_task_result(
    *,
    region_id: int,
    probe_id: int,
    task_id: int,
    created_at: datetime,
    speed_mbps: float | None = 12.5,
) -> int:
    """Insert a single ``task_results`` row with a custom ``created_at``.

    Returns the inserted row id so tests can assert on individual rows.
    """
    conn = await asyncpg.connect(_test_dsn())
    try:
        row = await conn.fetchrow(
            """
            INSERT INTO task_results
                (task_id, probe_id, region_id, speed_mbps, error, raw, created_at)
            VALUES ($1, $2, $3, $4, NULL, $5::jsonb, $6)
            RETURNING id
            """,
            task_id,
            probe_id,
            region_id,
            speed_mbps,
            json.dumps({"seed": True}),
            created_at,
        )
    finally:
        await conn.close()
    assert row is not None
    return int(row["id"])


async def _seed_task_result_for_probe(
    *,
    task_id: int,
    probe_id: int,
    region_id: int,
    created_at: datetime,
) -> int:
    """Variant that accepts an explicit probe id (used when reusing probes)."""
    return await _seed_task_result(
        region_id=region_id,
        probe_id=probe_id,
        task_id=task_id,
        created_at=created_at,
    )


async def _count(table: str) -> int:
    conn = await asyncpg.connect(_test_dsn())
    try:
        return int(await conn.fetchval(f"SELECT count(*) FROM {table}") or 0)  # noqa: S608
    finally:
        await conn.close()


async def _fetch_row(table: str, row_id: int) -> asyncpg.Record | None:
    conn = await asyncpg.connect(_test_dsn())
    try:
        return await conn.fetchrow(f"SELECT * FROM {table} WHERE id = $1", row_id)  # noqa: S608
    finally:
        await conn.close()


async def _fetch_all(table: str) -> list[asyncpg.Record]:
    conn = await asyncpg.connect(_test_dsn())
    try:
        return list(await conn.fetch(f"SELECT * FROM {table} ORDER BY id"))  # noqa: S608
    finally:
        await conn.close()


async def test_archive_old_results_moves_rows_older_than_one_day(app_instance: Any) -> None:
    """A 2-day-old row disappears from ``task_results`` and appears in archive."""
    db = app_instance.state.db
    db_pool: asyncpg.Pool = db.pool

    conn = await db_pool.acquire()
    try:
        region_id = int(
            (
                await conn.fetchrow(
                    "INSERT INTO regions (code, title) VALUES ($1, $2) RETURNING id",
                    "RU-MOW",
                    "Москва",
                )
            )["id"]
        )
        probe_id = int(
            (
                await conn.fetchrow(
                    """
                    INSERT INTO probes (alias, token, region_id)
                    VALUES ($1, $2, $3) RETURNING id
                    """,
                    "probe-1",
                    "token-archive-1",
                    region_id,
                )
            )["id"]
        )
        task_id = int(
            (
                await conn.fetchrow(
                    "INSERT INTO tasks (link, status) VALUES ($1, 'pending') RETURNING id",
                    "vless://old@example.com",
                )
            )["id"]
        )
    finally:
        await db_pool.release(conn)

    old_id = await _seed_task_result(
        region_id=region_id,
        probe_id=probe_id,
        task_id=task_id,
        created_at=datetime.now(tz=UTC) - timedelta(days=2),
    )

    moved = await archive_old_results(db)
    assert moved == 1, "exactly one backdated row should have been archived"

    assert await _count("task_results") == 0
    assert await _count("task_results_archive") == 1

    archived = await _fetch_row("task_results_archive", old_id)
    assert archived is not None
    assert archived["task_id"] == task_id
    assert archived["probe_id"] == probe_id
    assert archived["region_id"] == region_id
    assert archived["speed_mbps"] == pytest.approx(12.5)
    assert archived["error"] is None
    assert json.loads(archived["raw"]) == {"seed": True}
    assert archived["archived_at"] is not None


async def test_archive_old_results_keeps_recent_rows(app_instance: Any) -> None:
    """A row younger than the threshold must stay in ``task_results``."""
    db = app_instance.state.db
    db_pool: asyncpg.Pool = db.pool

    conn = await db_pool.acquire()
    try:
        region_id = int(
            (
                await conn.fetchrow(
                    "INSERT INTO regions (code, title) VALUES ($1, $2) RETURNING id",
                    "RU-MOW",
                    "Москва",
                )
            )["id"]
        )
        probe_id = int(
            (
                await conn.fetchrow(
                    """
                    INSERT INTO probes (alias, token, region_id)
                    VALUES ($1, $2, $3) RETURNING id
                    """,
                    "probe-1",
                    "token-archive-keep",
                    region_id,
                )
            )["id"]
        )
        task_id = int(
            (
                await conn.fetchrow(
                    "INSERT INTO tasks (link, status) VALUES ($1, 'pending') RETURNING id",
                    "vless://fresh@example.com",
                )
            )["id"]
        )
    finally:
        await db_pool.release(conn)

    fresh_id = await _seed_task_result(
        region_id=region_id,
        probe_id=probe_id,
        task_id=task_id,
        created_at=datetime.now(tz=UTC) - timedelta(hours=6),
    )

    moved = await archive_old_results(db)
    assert moved == 0, "no row should be moved when all are within the window"

    assert await _count("task_results") == 1
    assert await _count("task_results_archive") == 0

    fresh = await _fetch_row("task_results", fresh_id)
    assert fresh is not None
    assert fresh["task_id"] == task_id


async def test_archive_old_results_preserves_total_row_count(app_instance: Any) -> None:
    """Sum of ``task_results`` + ``task_results_archive`` is constant."""
    db = app_instance.state.db
    db_pool: asyncpg.Pool = db.pool

    conn = await db_pool.acquire()
    try:
        region_id = int(
            (
                await conn.fetchrow(
                    "INSERT INTO regions (code, title) VALUES ($1, $2) RETURNING id",
                    "RU-MOW",
                    "Москва",
                )
            )["id"]
        )
        probes: list[int] = []
        for i in range(4):
            probe_id = int(
                (
                    await conn.fetchrow(
                        """
                        INSERT INTO probes (alias, token, region_id)
                        VALUES ($1, $2, $3) RETURNING id
                        """,
                        f"probe-{i}",
                        f"token-archive-sum-{i}",
                        region_id,
                    )
                )["id"]
            )
            probes.append(probe_id)
        task_ids: list[int] = []
        for i in range(4):
            task_id = int(
                (
                    await conn.fetchrow(
                        "INSERT INTO tasks (link, status) VALUES ($1, 'pending') RETURNING id",
                        f"vless://sum-{i}@example.com",
                    )
                )["id"]
            )
            task_ids.append(task_id)
    finally:
        await db_pool.release(conn)

    now = datetime.now(tz=UTC)
    ages_hours = (2, 6, 25, 30)
    for probe_id, task_id, hours_ago in zip(probes, task_ids, ages_hours, strict=True):
        await _seed_task_result(
            region_id=region_id,
            probe_id=probe_id,
            task_id=task_id,
            created_at=now - timedelta(hours=hours_ago),
        )

    assert await _count("task_results") == 4
    total_before = await _count("task_results") + await _count("task_results_archive")

    moved = await archive_old_results(db)
    assert moved == 2, "the two rows older than a day should be archived"

    total_after = await _count("task_results") + await _count("task_results_archive")
    assert total_after == total_before, "row count must be conserved"

    rows = await _fetch_all("task_results")
    assert {row["created_at"].replace(tzinfo=UTC) for row in rows} == {
        now - timedelta(hours=2),
        now - timedelta(hours=6),
    }


async def test_archive_old_results_is_idempotent(app_instance: Any) -> None:
    """Running the mover twice in a row is a no-op the second time."""
    db = app_instance.state.db
    db_pool: asyncpg.Pool = db.pool

    conn = await db_pool.acquire()
    try:
        region_id = int(
            (
                await conn.fetchrow(
                    "INSERT INTO regions (code, title) VALUES ($1, $2) RETURNING id",
                    "RU-MOW",
                    "Москва",
                )
            )["id"]
        )
        probe_id = int(
            (
                await conn.fetchrow(
                    """
                    INSERT INTO probes (alias, token, region_id)
                    VALUES ($1, $2, $3) RETURNING id
                    """,
                    "probe-1",
                    "token-archive-idem",
                    region_id,
                )
            )["id"]
        )
        task_id = int(
            (
                await conn.fetchrow(
                    "INSERT INTO tasks (link, status) VALUES ($1, 'pending') RETURNING id",
                    "vless://idem@example.com",
                )
            )["id"]
        )
    finally:
        await db_pool.release(conn)

    await _seed_task_result(
        region_id=region_id,
        probe_id=probe_id,
        task_id=task_id,
        created_at=datetime.now(tz=UTC) - timedelta(days=3),
    )

    first = await archive_old_results(db)
    second = await archive_old_results(db)

    assert first == 1
    assert second == 0, "second run should be a no-op"
    assert await _count("task_results_archive") == 1


async def test_archive_old_results_handles_null_speed_mb_and_error(
    app_instance: Any,
) -> None:
    """Error-only results (no speed) must survive the move intact."""
    db = app_instance.state.db
    db_pool: asyncpg.Pool = db.pool

    conn = await db_pool.acquire()
    try:
        region_id = int(
            (
                await conn.fetchrow(
                    "INSERT INTO regions (code, title) VALUES ($1, $2) RETURNING id",
                    "RU-MOW",
                    "Москва",
                )
            )["id"]
        )
        probe_id = int(
            (
                await conn.fetchrow(
                    """
                    INSERT INTO probes (alias, token, region_id)
                    VALUES ($1, $2, $3) RETURNING id
                    """,
                    "probe-1",
                    "token-archive-null",
                    region_id,
                )
            )["id"]
        )
        task_id = int(
            (
                await conn.fetchrow(
                    "INSERT INTO tasks (link, status) VALUES ($1, 'pending') RETURNING id",
                    "vless://err@example.com",
                )
            )["id"]
        )
    finally:
        await db_pool.release(conn)

    conn = await db_pool.acquire()
    try:
        row_id = int(
            (
                await conn.fetchrow(
                    """
                    INSERT INTO task_results
                        (task_id, probe_id, region_id, speed_mbps, error, raw, created_at)
                    VALUES ($1, $2, $3, NULL, $4, $5::jsonb, $6)
                    RETURNING id
                    """,
                    task_id,
                    probe_id,
                    region_id,
                    "connection timed out",
                    json.dumps({"stderr": "boom"}),
                    datetime.now(tz=UTC) - timedelta(days=5),
                )
            )["id"]
        )
    finally:
        await db_pool.release(conn)

    moved = await archive_old_results(db)
    assert moved == 1

    archived = await _fetch_row("task_results_archive", row_id)
    assert archived is not None
    assert archived["speed_mbps"] is None
    assert archived["error"] == "connection timed out"
    assert json.loads(archived["raw"]) == {"stderr": "boom"}


async def test_archive_lag_seconds_is_none_when_archive_is_empty(
    app_instance: Any,
) -> None:
    """``archive_lag_seconds`` must return ``None`` before any archive run."""
    db = app_instance.state.db
    assert await archive_lag_seconds(db) is None


async def test_archive_lag_seconds_is_positive_after_archive(app_instance: Any) -> None:
    """After the mover runs, the lag is a small positive number of seconds."""
    db = app_instance.state.db
    db_pool: asyncpg.Pool = db.pool

    conn = await db_pool.acquire()
    try:
        region_id = int(
            (
                await conn.fetchrow(
                    "INSERT INTO regions (code, title) VALUES ($1, $2) RETURNING id",
                    "RU-MOW",
                    "Москва",
                )
            )["id"]
        )
        probe_id = int(
            (
                await conn.fetchrow(
                    """
                    INSERT INTO probes (alias, token, region_id)
                    VALUES ($1, $2, $3) RETURNING id
                    """,
                    "probe-1",
                    "token-archive-lag",
                    region_id,
                )
            )["id"]
        )
        task_id = int(
            (
                await conn.fetchrow(
                    "INSERT INTO tasks (link, status) VALUES ($1, 'pending') RETURNING id",
                    "vless://lag@example.com",
                )
            )["id"]
        )
    finally:
        await db_pool.release(conn)

    await _seed_task_result(
        region_id=region_id,
        probe_id=probe_id,
        task_id=task_id,
        created_at=datetime.now(tz=UTC) - timedelta(days=2),
    )

    assert await archive_lag_seconds(db) is None
    await archive_old_results(db)

    lag = await archive_lag_seconds(db)
    assert lag is not None
    assert 0.0 <= lag < 30.0, f"lag should be sub-second-after to a few seconds, got {lag}"


__all__ = [
    "test_archive_old_results_moves_rows_older_than_one_day",
    "test_archive_old_results_keeps_recent_rows",
    "test_archive_old_results_preserves_total_row_count",
    "test_archive_old_results_is_idempotent",
    "test_archive_old_results_handles_null_speed_mb_and_error",
    "test_archive_lag_seconds_is_none_when_archive_is_empty",
    "test_archive_lag_seconds_is_positive_after_archive",
]
