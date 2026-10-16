"""``/api/v1/tasks`` endpoints.

Exposes:

* ``POST /api/v1/tasks`` — enqueue a new speedtest task (rate-limited to
  ``10/hour`` per source IP).
* ``GET  /api/v1/tasks/{task_id}`` — fetch a single task with its
  per-probe results (rate-limited to ``100/minute`` per IP).
* ``GET  /api/v1/tasks`` — paginated task list, newest first.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Annotated, Any
from urllib.parse import urlparse

from asyncpg import Connection
from fastapi import APIRouter, HTTPException, Path, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field, field_validator

from xspeed_server.app.core.ratelimit import limiter
from xspeed_server.app.deps import PoolDep

router = APIRouter(prefix="/tasks", tags=["tasks"])

VLESS_LINK_PATTERN = r"^vless://[0-9a-f-]{36}@[\w.-]+:\d+\?.*$"


class TaskCreate(BaseModel):
    """Payload accepted by ``POST /api/v1/tasks``."""

    model_config = ConfigDict(extra="forbid")

    link: str = Field(
        ...,
        min_length=1,
        max_length=2048,
        pattern=VLESS_LINK_PATTERN,
        description="vless:// URI; full schema is validated by FastAPI's regex.",
    )
    destination_id: int = Field(
        ...,
        ge=1,
        description=(
            "Speedtest target chosen from the destinations catalogue. "
            "Probes use this URL instead of their default ``SPEEDTEST_URL``."
        ),
    )
    probe_tokens: list[str] | None = Field(
        default=None,
        description=(
            "Optional whitelist of probe tokens that should run the task. "
            "When omitted the task is added to the public pool."
        ),
    )

    @field_validator("probe_tokens")
    @classmethod
    def _strip_probe_tokens(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        cleaned = [token.strip() for token in value if token and token.strip()]
        if not cleaned:
            return None
        return cleaned


class TaskCreatedResponse(BaseModel):
    """Response returned by ``POST /api/v1/tasks``."""

    id: int
    status: str
    destination_id: int
    destination_url: str


class TaskResultItem(BaseModel):
    """Per-probe result for a task."""

    id: int
    probe_id: int
    probe_alias: str
    region_id: int
    region_code: str
    region_title: str
    speed_mbps: float | None
    error: str | None
    created_at: datetime


class TaskDetail(BaseModel):
    """Response returned by ``GET /api/v1/tasks/{id}``."""

    id: int
    link: str
    address: str | None
    status: str
    probe_tokens: list[str] | None
    destination_id: int | None
    destination_name: str | None
    destination_url: str | None
    created_at: datetime
    completed_at: datetime | None
    results: list[TaskResultItem]


class TaskSummary(BaseModel):
    """Single row in the paginated task list."""

    id: int
    link: str
    address: str | None
    status: str
    destination_id: int | None
    destination_name: str | None
    destination_url: str | None
    created_at: datetime
    completed_at: datetime | None


class TaskList(BaseModel):
    """Envelope for ``GET /api/v1/tasks``."""

    items: list[TaskSummary]
    limit: int
    offset: int
    total: int


async def _insert_task(
    conn: Connection,
    link: str,
    destination_id: int,
    probe_tokens: list[str] | None,
) -> int:
    """Insert a new task row and return its primary key.

    Uses raw SQL via asyncpg so the response path is independent from the
    SQLAlchemy declarative models. ``probe_tokens`` is JSON-encoded
    before being passed to asyncpg because the default driver codec
    expects a string for ``jsonb`` parameters.
    """
    try:
        row = await conn.fetchrow(
            """
            INSERT INTO tasks (link, destination_id, status, probe_tokens, created_at)
            VALUES ($1, $2, 'pending', $3::jsonb, now())
            RETURNING id
            """,
            link,
            destination_id,
            None if probe_tokens is None else json.dumps(probe_tokens),
        )
    except Exception as exc:  # noqa: BLE001 - translate FK violation to 404
        if _is_foreign_key_violation(exc):
            raise HTTPException(status_code=404, detail="Destination not found") from exc
        raise
    if row is None:  # pragma: no cover - RETURNING is guaranteed by Postgres
        raise HTTPException(status_code=500, detail="Failed to create task")
    return int(row["id"])


async def _resolve_destination(conn: Connection, destination_id: int) -> tuple[str, str] | None:
    """Return ``(name, url)`` for ``destination_id`` or ``None`` when missing."""
    row = await conn.fetchrow(
        "SELECT name, url FROM destinations WHERE id = $1",
        destination_id,
    )
    if row is None:
        return None
    return str(row["name"]), str(row["url"])


async def _fetch_task_with_results(conn: Connection, task_id: int) -> dict[str, Any] | None:
    """Fetch the task header plus its results in a single connection checkout."""
    task_row = await conn.fetchrow(
        """
        SELECT id, link, status, probe_tokens, destination_id, created_at, completed_at
        FROM tasks
        WHERE id = $1
        """,
        task_id,
    )
    if task_row is None:
        return None
    destination_id = task_row["destination_id"]
    destination: tuple[str, str] | None = None
    if destination_id is not None:
        destination = await _resolve_destination(conn, int(destination_id))
    result_rows = await conn.fetch(
        """
        SELECT tr.id,
               tr.probe_id,
               p.alias AS probe_alias,
               tr.region_id,
               r.code AS region_code,
               r.title AS region_title,
               tr.speed_mbps,
               tr.error,
               tr.created_at
        FROM task_results tr
        JOIN probes p ON p.id = tr.probe_id
        JOIN regions r ON r.id = tr.region_id
        WHERE tr.task_id = $1
        ORDER BY tr.created_at ASC
        """,
        task_id,
    )
    return {
        "id": task_row["id"],
        "link": task_row["link"],
        "address": _extract_address(str(task_row["link"])),
        "status": task_row["status"],
        "probe_tokens": list(task_row["probe_tokens"]) if task_row["probe_tokens"] else None,
        "destination_id": destination_id,
        "destination_name": destination[0] if destination else None,
        "destination_url": destination[1] if destination else None,
        "created_at": task_row["created_at"],
        "completed_at": task_row["completed_at"],
        "results": [dict(row) for row in result_rows],
    }


def _extract_address(link: str) -> str | None:
    """Return ``host[:port]`` parsed from a vless:// URI, or None on failure.

    The vless URI shape is ``vless://<UUID>@<host>:<port>?<query>#<name>``;
    :func:`urllib.parse.urlparse` already handles the userinfo split so
    :attr:`ParseResult.hostname` and :attr:`ParseResult.port` are exactly
    what we need to surface in the UI. We bracket an IPv6 host (no port
    is exposed by ``urlparse`` in that case but the operator can still
    see the address).
    """
    try:
        parsed = urlparse(link)
    except ValueError:
        return None
    if parsed.scheme != "vless":
        return None
    host = parsed.hostname
    if not host:
        return None
    if parsed.port is not None:
        return f"{host}:{parsed.port}"
    return host


def _is_foreign_key_violation(exc: BaseException) -> bool:
    """Return True when ``exc`` is an asyncpg foreign-key violation."""
    sqlstate = getattr(exc, "sqlstate", None)
    return sqlstate == "23503"


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=TaskCreatedResponse,
    summary="Enqueue a new speedtest task.",
)
@limiter.limit("10/hour")
async def create_task(
    request: Request,  # required by slowapi to extract the client IP
    payload: TaskCreate,
    pool: PoolDep,
) -> TaskCreatedResponse:
    """Validate the vless link, insert a ``pending`` row and return its id."""
    async with pool.acquire() as conn:
        destination = await _resolve_destination(conn, payload.destination_id)
        if destination is None:
            raise HTTPException(status_code=404, detail="Destination not found")
        task_id = await _insert_task(
            conn,
            payload.link,
            payload.destination_id,
            payload.probe_tokens,
        )
        destination_name, destination_url = destination
    hub = request.app.state.hub
    await hub.broadcast({"type": "task.created", "payload": {"id": task_id, "status": "pending"}})
    return TaskCreatedResponse(
        id=task_id,
        status="pending",
        destination_id=payload.destination_id,
        destination_url=destination_url,
    )


@router.get(
    "/{task_id}",
    response_model=TaskDetail,
    summary="Fetch a task along with its per-probe results.",
)
@limiter.limit("100/minute")
async def get_task(
    request: Request,  # required by slowapi to extract the client IP
    task_id: Annotated[int, Path(ge=1)],
    pool: PoolDep,
) -> TaskDetail:
    """Return the task header and its results or 404 if the id is unknown."""
    async with pool.acquire() as conn:
        record = await _fetch_task_with_results(conn, task_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return TaskDetail.model_validate(record)


@router.get(
    "",
    response_model=TaskList,
    summary="List tasks, newest first, with offset/limit pagination.",
)
@limiter.limit("100/minute")
async def list_tasks(
    request: Request,  # required by slowapi to extract the client IP
    pool: PoolDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> TaskList:
    """Return a page of tasks ordered by ``created_at`` desc plus the total count."""
    async with pool.acquire() as conn:
        total = await conn.fetchval("SELECT count(*) FROM tasks")
        rows = await conn.fetch(
            """
            SELECT t.id, t.link, t.status, t.destination_id,
                   d.name AS destination_name,
                   d.url  AS destination_url,
                   t.created_at, t.completed_at
            FROM tasks t
            LEFT JOIN destinations d ON d.id = t.destination_id
            ORDER BY t.created_at DESC, t.id DESC
            LIMIT $1 OFFSET $2
            """,
            limit,
            offset,
        )
    items = [TaskSummary.model_validate(_row_to_summary(row)) for row in rows]
    return TaskList(items=items, limit=limit, offset=offset, total=int(total or 0))


def _row_to_summary(row: Any) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "link": str(row["link"]),
        "address": _extract_address(str(row["link"])),
        "status": str(row["status"]),
        "destination_id": row["destination_id"],
        "destination_name": row["destination_name"],
        "destination_url": row["destination_url"],
        "created_at": row["created_at"],
        "completed_at": row["completed_at"],
    }


__all__ = [
    "router",
    "TaskCreate",
    "TaskCreatedResponse",
    "TaskDetail",
    "TaskList",
    "TaskSummary",
    "TaskResultItem",
    "VLESS_LINK_PATTERN",
]
