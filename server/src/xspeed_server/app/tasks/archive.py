"""Periodic archival of cold ``task_results`` rows.

``task_results`` is the hot table queried on every GET against
``/api/v1/tasks/{id}`` and on every frontend map refresh, so its size
must stay bounded. Once a row is older than one day it is no longer
relevant for live dashboards and is moved (not copied) into
``task_results_archive``, the cold-storage mirror defined by the
``0003_archive`` Alembic migration.

* :func:`archive_old_results` is the transactional mover: a single
  CTE deletes qualifying rows from ``task_results`` and inserts them
  into ``task_results_archive`` with an ``archived_at`` stamp.
* :func:`archive_lag_seconds` is the freshness probe used by the
  future ``/admin/stats`` endpoint: it returns the age of the most
  recent ``archived_at`` row, or ``None`` when the archive has never
  run.
* :func:`start_archive_scheduler` wires the mover into a 60-minute
  :class:`apscheduler.schedulers.asyncio.AsyncIOScheduler` so the
  FastAPI lifespan can register it as a background job.
* :func:`run_cli` (exposed via :mod:`xspeed_server.app.tasks.cli`) makes
  it possible to trigger the mover manually with
  ``python -m xspeed_server.app.tasks.cli`` (also exposed as
  ``make archive``).
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from apscheduler.schedulers.asyncio import AsyncIOScheduler

    from xspeed_server.app.db import Database

logger = logging.getLogger(__name__)


ARCHIVE_INTERVAL_MINUTES: int = 60
"""How often the archive scheduler runs ``archive_old_results``."""

ARCHIVE_AGE_SECONDS: int = 24 * 60 * 60
"""How old a ``task_results`` row must be before the mover picks it up.

Expressed in seconds so it can be passed straight to PostgreSQL's
``make_interval(secs => $1)`` without any string-based SQL composition.
"""


_ARCHIVE_SQL = """
WITH moved AS (
    DELETE FROM task_results
    WHERE created_at < now() - make_interval(secs => $1)
    RETURNING id, task_id, probe_id, region_id, speed_mbps, error, raw, created_at
)
INSERT INTO task_results_archive
    (id, task_id, probe_id, region_id, speed_mbps, error, raw, created_at, archived_at)
SELECT id, task_id, probe_id, region_id, speed_mbps, error, raw, created_at, now()
FROM moved
"""


_LAG_SQL = """
SELECT
    EXTRACT(EPOCH FROM (now() - MAX(archived_at)))::double precision AS lag_seconds
FROM task_results_archive
"""


async def archive_old_results(db: Database) -> int:
    """Move ``task_results`` rows older than one day into ``task_results_archive``.

    The delete + insert happen inside a single transaction so the
    archive either runs to completion or leaves both tables untouched
    on error. Returns the number of rows that were moved; ``0`` means
    nothing was eligible (the normal steady state once the archive is
    caught up).
    """
    async with db.pool.acquire() as conn:
        async with conn.transaction():
            tag = await conn.execute(_ARCHIVE_SQL, ARCHIVE_AGE_SECONDS)
    moved = _rows_archived_from_command_tag(tag)
    logger.info("archive_old_results moved %d row(s) to task_results_archive", moved)
    return moved


def _rows_archived_from_command_tag(tag: Any) -> int:
    """Extract the row count from an asyncpg ``INSERT … SELECT`` command tag.

    asyncpg returns tags like ``"INSERT 0 42"``; the second token is the
    affected row count for ``INSERT`` statements and the same trailing
    field is used by ``DELETE``. ``"INSERT 0 0"`` is the steady-state
    success case where the CTE moved nothing.
    """
    if tag is None:
        return 0
    parts = str(tag).split()
    if len(parts) < 3:
        return 0
    try:
        return int(parts[-1])
    except ValueError:
        return 0


async def archive_lag_seconds(db: Database) -> float | None:
    """Seconds elapsed since the last archive run, or ``None`` if never run.

    Computed as ``now() - MAX(archived_at)`` so the future
    ``/admin/stats`` endpoint can surface archive freshness without
    needing an in-memory counter. Returns ``None`` when
    ``task_results_archive`` is empty (the table has never been
    populated).
    """
    async with db.pool.acquire() as conn:
        lag = await conn.fetchval(_LAG_SQL)
    if lag is None:
        return None
    return float(lag)


def start_archive_scheduler(db: Database) -> AsyncIOScheduler | None:
    """Start the APScheduler instance used to run :func:`archive_old_results`.

    Returns the running scheduler, or ``None`` if ``apscheduler`` is not
    installed (so the application can still boot in lean test images).
    """
    try:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
    except ImportError:  # pragma: no cover - optional dependency in tests
        logger.warning("apscheduler is not installed; archive job will not run")
        return None
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        archive_old_results,
        "interval",
        minutes=ARCHIVE_INTERVAL_MINUTES,
        args=[db],
    )
    scheduler.start()
    logger.info("archive scheduler started (interval=%dm)", ARCHIVE_INTERVAL_MINUTES)
    return scheduler


async def run_once(db: Database) -> int:
    """Run a single archive pass and report the result to stdout.

    Used by :mod:`xspeed_server.app.tasks.cli` so
    ``python -m xspeed_server.app.tasks.cli`` (and ``make archive``)
    trigger exactly one mover iteration instead of starting the
    long-running scheduler.
    """
    moved = await archive_old_results(db)
    lag = await archive_lag_seconds(db)
    lag_str = f"{lag:.1f}s" if lag is not None else "n/a"
    print(f"archived={moved} lag_seconds={lag_str}")
    return 0


async def _amain() -> int:
    """Build a :class:`Database` from settings and execute :func:`run_once`."""
    from xspeed_server.app.config import get_settings
    from xspeed_server.app.db import Database as _Database

    settings = get_settings()
    db = await _Database.create(settings)
    try:
        return await run_once(db)
    finally:
        await db.dispose()


def run_cli() -> int:
    """Synchronous entry point used by ``python -m xspeed_server.app.tasks.cli``."""
    return asyncio.run(_amain())


__all__ = [
    "ARCHIVE_AGE_SECONDS",
    "ARCHIVE_INTERVAL_MINUTES",
    "archive_lag_seconds",
    "archive_old_results",
    "run_cli",
    "run_once",
    "start_archive_scheduler",
]
