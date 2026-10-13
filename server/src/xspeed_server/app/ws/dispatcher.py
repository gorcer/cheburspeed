"""Task dispatching, completion checking and stale-task sweeping.

The dispatcher is the bridge between the WebSocket :class:`Hub` and the
``tasks`` table. Three concerns live here:

* :func:`claim_task` atomically picks the next eligible pending task
  for a probe (``SELECT … FOR UPDATE SKIP LOCKED``), marks it as
  ``running`` and returns the data needed to push a
  ``task.assigned`` message over the socket.
* :func:`check_completion` decides whether the just-arrived result is
  enough to flip the task to ``completed`` (or to leave it
  ``pending`` so the next probe can pick it up when the task requires
  multiple probes).
* :func:`sweep_stale_running_tasks` is the safety net for probes that
  accepted a task but never sent a result; it is scheduled by
  :func:`start_sweeper` and resets tasks that have been ``running``
  for more than five minutes back to ``pending``.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Any

import asyncpg

from xspeed_server.app.db import Database
from xspeed_server.app.ws.hub import Hub
from xspeed_server.app.ws.protocol import PROTOCOL_VERSION, TaskAssignedMessage

logger = logging.getLogger(__name__)

STALE_RUNNING_AFTER_SECONDS: int = 5 * 60

SWEEP_INTERVAL_SECONDS: float = 30.0


@dataclass(slots=True)
class ClaimedTask:
    """A pending task pulled from the queue and reserved for a probe."""

    id: int
    link: str


async def claim_task(conn: asyncpg.Connection, probe_token: str) -> ClaimedTask | None:
    """Atomically reserve the oldest pending task for ``probe_token``.

    The eligibility predicate is:

    * ``status = 'pending'`` — never pick up a task that has already
      been claimed.
    * ``probe_tokens IS NULL`` — public tasks are open to any probe.
    * ``probe_tokens ? $1`` — the JSONB ``?`` operator returns true
      when the array contains the literal value. This is how the
      whitelist per task is matched against the connecting probe.
    * ``ORDER BY created_at`` — oldest pending tasks are served first.
    * ``FOR UPDATE SKIP LOCKED`` — concurrent dispatchers (one per
      connecting probe) each take a different row, never block.

    On success the row is flipped to ``running`` with
    ``assigned_to = probe_token`` and ``started_at = now()`` inside
    the same transaction.
    """
    async with conn.transaction():
        row = await conn.fetchrow(
            """
            SELECT id, link
            FROM tasks
            WHERE status = 'pending'
              AND (probe_tokens IS NULL OR probe_tokens ? $1)
            ORDER BY created_at, id
            FOR UPDATE SKIP LOCKED
            LIMIT 1
            """,
            probe_token,
        )
        if row is None:
            return None
        task_id = int(row["id"])
        link = str(row["link"])
        await conn.execute(
            """
            UPDATE tasks
            SET status = 'running',
                assigned_to = $2,
                started_at = now()
            WHERE id = $1
            """,
            task_id,
            probe_token,
        )
    logger.info("dispatched task %s to probe %s", task_id, _mask_token(probe_token))
    return ClaimedTask(id=task_id, link=link)


async def check_completion(conn: asyncpg.Connection, task_id: int) -> bool:
    """Mark ``task_id`` as ``completed`` when all expected results are in.

    "Expected" is the cardinality of the ``probe_tokens`` whitelist:
    when the whitelist is missing the task is public and a single
    result is enough. When the whitelist contains more probes, the
    task is only flipped to ``completed`` once every distinct probe
    in the whitelist has reported a result.

    Tasks that still need more results are reset to ``pending`` so
    the next eligible probe can claim them.

    Returns ``True`` if the task was flipped to ``completed``,
    ``False`` otherwise.
    """
    task_row = await conn.fetchrow(
        "SELECT probe_tokens FROM tasks WHERE id = $1",
        task_id,
    )
    if task_row is None:
        return False
    raw_tokens = task_row["probe_tokens"]
    if raw_tokens is None:
        expected = 1
    elif isinstance(raw_tokens, str):
        # asyncpg returns JSONB columns as a JSON-encoded string by
        # default; parse it so ``len`` reflects the whitelist size.
        expected = max(len(json.loads(raw_tokens)), 1)
    else:
        expected = max(len(raw_tokens), 1)
    reported = await conn.fetchval(
        "SELECT count(DISTINCT probe_id) FROM task_results WHERE task_id = $1",
        task_id,
    )
    reported = int(reported or 0)
    if reported >= expected:
        await conn.execute(
            """
            UPDATE tasks
            SET status = 'completed',
                completed_at = now(),
                assigned_to = NULL,
                started_at = NULL
            WHERE id = $1
            """,
            task_id,
        )
        logger.info("task %s completed (%d/%d results)", task_id, reported, expected)
        return True
    if expected > 1:
        await conn.execute(
            """
            UPDATE tasks
            SET status = 'pending',
                assigned_to = NULL,
                started_at = NULL
            WHERE id = $1
            """,
            task_id,
        )
        logger.debug("task %s awaiting more results (%d/%d)", task_id, reported, expected)
    return False


async def record_result(
    conn: asyncpg.Connection,
    *,
    task_id: int,
    probe_id: int,
    region_id: int,
    speed_mbps: float | None,
    error: str | None,
    raw: dict[str, Any],
) -> None:
    """Persist a probe outcome into ``task_results``.

    The unique constraint on ``(task_id, probe_id)`` is honoured via
    ``ON CONFLICT … DO UPDATE`` so a probe retrying after a network
    blip overwrites the previous attempt rather than producing a
    duplicate row.
    """
    await conn.execute(
        """
        INSERT INTO task_results
            (task_id, probe_id, region_id, speed_mbps, error, raw)
        VALUES ($1, $2, $3, $4, $5, $6::jsonb)
        ON CONFLICT (task_id, probe_id) DO UPDATE
        SET speed_mbps = EXCLUDED.speed_mbps,
            error      = EXCLUDED.error,
            raw        = EXCLUDED.raw,
            created_at = now()
        """,
        task_id,
        probe_id,
        region_id,
        speed_mbps,
        error,
        json.dumps(raw),
    )


async def sweep_stale_running_tasks(db: Database) -> int:
    """Reset ``running`` tasks that have stalled for too long.

    "Stalled" means: ``status = 'running'`` AND
    ``started_at < now() - 5 minutes`` AND no row in
    ``task_results`` for that task yet. The atomic UPDATE … RETURNING
    pattern lets the sweeper report the exact set of tasks it
    rescued in a single round-trip.
    """
    async with db.pool.acquire() as conn:
        rows = await conn.fetch(
            """
            UPDATE tasks
            SET status = 'pending',
                assigned_to = NULL,
                started_at = NULL
            WHERE status = 'running'
              AND started_at IS NOT NULL
              AND started_at < now() - ($1 || ' seconds')::interval
              AND NOT EXISTS (
                  SELECT 1 FROM task_results WHERE task_id = tasks.id
              )
            RETURNING id
            """,
            str(STALE_RUNNING_AFTER_SECONDS),
        )
    if rows:
        logger.warning(
            "sweeper reset %d stale running task(s): %s",
            len(rows),
            [int(r["id"]) for r in rows],
        )
    return len(rows)


async def dispatch_to_probe(db: Database, hub: Hub, probe_token: str) -> bool:
    """Try to claim a task for ``probe_token`` and push it over the socket.

    Returns ``True`` when a ``task.assigned`` message was actually
    delivered; ``False`` when the probe is offline or the queue is
    empty. The boolean exists so the WebSocket handler can decide
    whether to keep the probe idle or to do something else.
    """
    async with db.pool.acquire() as conn:
        claimed = await claim_task(conn, probe_token)
    if claimed is None:
        return False
    message = TaskAssignedMessage(
        task_id=claimed.id,
        link=claimed.link,
        protocol_version=PROTOCOL_VERSION,
    ).model_dump()
    return await hub.send(probe_token, message)


async def _sweeper_loop(db: Database) -> None:
    """Periodic task driving :func:`sweep_stale_running_tasks`."""
    try:
        while True:
            try:
                await sweep_stale_running_tasks(db)
            except asyncio.CancelledError:  # noqa: PERF203 - explicit cancellation
                raise
            except Exception:  # noqa: BLE001 - logged for ops visibility
                logger.exception("sweeper iteration failed")
            await asyncio.sleep(SWEEP_INTERVAL_SECONDS)
    except asyncio.CancelledError:
        logger.info("sweeper task cancelled")
        raise


def start_sweeper(db: Database) -> asyncio.Task[None]:
    """Schedule the background sweeper and return its handle.

    The caller is responsible for cancelling the task on shutdown.
    """
    return asyncio.create_task(_sweeper_loop(db), name="xspeed-ws-sweeper")


def _mask_token(token: str) -> str:
    """Return a non-secret log-friendly version of ``token``."""
    if len(token) <= 6:
        return "***"
    return f"{token[:3]}…{token[-3:]}"


__all__ = [
    "ClaimedTask",
    "STALE_RUNNING_AFTER_SECONDS",
    "SWEEP_INTERVAL_SECONDS",
    "check_completion",
    "claim_task",
    "dispatch_to_probe",
    "record_result",
    "start_sweeper",
    "sweep_stale_running_tasks",
]