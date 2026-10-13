"""Background tasks (filled in by DAT-325.7).

The :func:`archive_old_results` coroutine is a no-op placeholder; the
real implementation (moving ``task_results`` older than one day into
``task_results_archive``) is shipped by the archiving task. The
:func:`start_archive_scheduler` factory exposes the
:class:`apscheduler.schedulers.asyncio.AsyncIOScheduler` used to run
that job on a 60-minute interval so the lifespan can wire it up
without depending on the upcoming task.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from apscheduler.schedulers.asyncio import AsyncIOScheduler

    from xspeed_server.app.db import Database

logger = logging.getLogger(__name__)


async def archive_old_results(db: Database) -> None:  # pragma: no cover - placeholder
    """Move ``task_results`` older than one day into ``task_results_archive``.

    The real implementation lives in the archiving task (DAT-325.7);
    this stub keeps the call site valid.
    """
    logger.debug("archive_old_results stub invoked (no-op until DAT-325.7)")


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
    scheduler.add_job(archive_old_results, "interval", minutes=60, args=[db])
    scheduler.start()
    logger.info("archive scheduler started (interval=60m)")
    return scheduler


__all__ = ["archive_old_results", "start_archive_scheduler"]
