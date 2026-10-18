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
    destination_url: str | None
    probe_public_ip: str | None


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
    the same transaction. The destination URL is joined from
    ``destinations`` so the dispatcher can pass an override to the
    probe instead of letting it fall back to its
    ``settings.speedtest_url``. The probe's most recently seen IP is
    attached so the probe can refuse to run a speedtest against its
    own public address (DAT-459).
    """
    async with conn.transaction():
        row = await conn.fetchrow(
            """
            SELECT t.id, t.link, d.url AS destination_url, p.ip AS probe_ip
            FROM tasks t
            LEFT JOIN destinations d ON d.id = t.destination_id
            CROSS JOIN probes p
            WHERE t.status = 'pending'
              AND p.token = $1
              AND NOT EXISTS (
                  SELECT 1
                  FROM task_results tr
                  JOIN probes p2 ON p2.id = tr.probe_id
                  WHERE tr.task_id = t.id AND p2.token = $1
              )
              AND (
                  (t.probe_tokens IS NULL AND p.is_public)
                  OR t.probe_tokens ? $1
              )
            ORDER BY t.created_at, t.id
            FOR UPDATE OF t SKIP LOCKED
            LIMIT 1
            """,
            probe_token,
        )
        if row is None:
            return None
        task_id = int(row["id"])
        link = str(row["link"])
        destination_url: str | None = row["destination_url"]
        probe_public_ip: str | None = row["probe_ip"]
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
    return ClaimedTask(
        id=task_id,
        link=link,
        destination_url=destination_url,
        probe_public_ip=probe_public_ip,
    )


async def check_completion(conn: asyncpg.Connection, task_id: int) -> bool:
    """Mark ``task_id`` as ``completed`` when all expected results are in.

    Expected results are resolved from the server-side probe registry:
    every public probe for a public task, or every probe whose token is
    listed in a targeted task. A probe with an existing result is excluded
    from subsequent claims, so a task progresses through its recipients.

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
        expected = await conn.fetchval("SELECT count(*) FROM probes WHERE is_public")
    else:
        tokens = json.loads(raw_tokens) if isinstance(raw_tokens, str) else raw_tokens
        expected = await conn.fetchval(
            "SELECT count(*) FROM probes WHERE token = ANY($1::text[])",
            list(tokens),
        )
    expected = int(expected or 0)
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
        destination_url=claimed.destination_url,
        protocol_version=PROTOCOL_VERSION,
        probe_public_ip=claimed.probe_public_ip,
    ).model_dump()
    delivered = await hub.send(probe_token, message)
    if delivered:
        return True
    async with db.pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE tasks
            SET status = 'pending', assigned_to = NULL, started_at = NULL
            WHERE id = $1 AND status = 'running' AND assigned_to = $2
            """,
            claimed.id,
            probe_token,
        )
    return False


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
