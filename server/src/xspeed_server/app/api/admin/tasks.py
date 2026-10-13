"""``/api/v1/admin/tasks`` endpoints (admin-protected read).

The admin panel only needs filtered listing for tasks; creation
goes through the public ``/api/v1/tasks`` route (rate-limited) and
the WebSocket Hub owns the lifecycle. The supported filters are:

* ``status`` — exact match on ``tasks.status``.
* ``from``/``to`` — inclusive ``created_at`` range (ISO 8601).
* ``region`` — restrict to tasks that have at least one result in
  the given region code.
* ``limit``/``offset`` — pagination, capped at 200 per page.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from pydantic import BaseModel, ConfigDict

from xspeed_server.app.core.dependencies import get_admin_user
from xspeed_server.app.deps import PoolDep

router = APIRouter(
    prefix="/admin/tasks",
    tags=["admin-tasks"],
    dependencies=[Depends(get_admin_user)],
)

__all__ = ["router", "AdminTaskSummary", "AdminTaskList", "AdminTaskDetail", "AdminTaskResultItem"]


_ALLOWED_STATUSES = {"pending", "running", "completed", "failed"}


class AdminTaskResultItem(BaseModel):
    """Per-probe result attached to an admin task detail."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    task_id: int
    probe_id: int
    probe_alias: str
    region_id: int
    region_code: str
    region_title: str
    speed_mbps: float | None
    error: str | None
    created_at: datetime


class AdminTaskSummary(BaseModel):
    """One row in the admin task list."""

    id: int
    link: str
    status: str
    probe_tokens: list[str] | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    result_count: int
    error_count: int


class AdminTaskList(BaseModel):
    """Envelope returned by ``GET /api/v1/admin/tasks``."""

    items: list[AdminTaskSummary]
    limit: int
    offset: int
    total: int


class AdminTaskDetail(BaseModel):
    """Response returned by ``GET /api/v1/admin/tasks/{id}``."""

    id: int
    link: str
    status: str
    probe_tokens: list[str] | None
    assigned_to: str | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    results: list[AdminTaskResultItem]


def _parse_iso_datetime(value: str | None) -> datetime | None:
    """Parse an ISO 8601 datetime string, tolerating a trailing ``Z``."""
    if not value:
        return None
    candidate = value.replace("Z", "+00:00")
    return datetime.fromisoformat(candidate)


def _validate_status(value: str) -> str:
    if value not in _ALLOWED_STATUSES:
        raise HTTPException(
            status_code=422,
            detail=f"status must be one of {sorted(_ALLOWED_STATUSES)}",
        )
    return value


@router.get(
    "",
    response_model=AdminTaskList,
    summary="List tasks with admin-only filters.",
)
async def list_admin_tasks(
    pool: PoolDep,
    status: Annotated[str | None, Query(pattern=r"^(pending|running|completed|failed)$")] = None,
    from_: Annotated[
        datetime | None,
        Query(alias="from", description="Lower bound (inclusive) on created_at."),
    ] = None,
    to: Annotated[
        datetime | None,
        Query(description="Upper bound (inclusive) on created_at."),
    ] = None,
    region: Annotated[
        str | None,
        Query(min_length=1, max_length=64, description="Region code filter."),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> AdminTaskList:
    """Return a filtered page of tasks with aggregated result counters.

    The query is built dynamically so each filter adds one
    ``WHERE``/``HAVING`` clause without string-concatenating any
    user-controlled value into the SQL.
    """
    if from_ is not None and to is not None and from_ > to:
        raise HTTPException(status_code=422, detail="'from' must be <= 'to'")
    if status is not None:
        _validate_status(status)

    where: list[str] = []
    params: list[Any] = []
    if status is not None:
        params.append(status)
        where.append(f"t.status = ${len(params)}")
    if from_ is not None:
        params.append(from_)
        where.append(f"t.created_at >= ${len(params)}")
    if to is not None:
        params.append(to)
        where.append(f"t.created_at <= ${len(params)}")
    if region is not None:
        params.append(region)
        where.append(
            f"EXISTS (SELECT 1 FROM task_results tr "  # noqa: S608
            f"JOIN regions r ON r.id = tr.region_id "
            f"WHERE tr.task_id = t.id AND r.code = ${len(params)})"
        )
    where_clause = f"WHERE {' AND '.join(where)}" if where else ""  # noqa: S608

    list_query = f"""
        SELECT t.id,
               t.link,
               t.status,
               t.probe_tokens,
               t.created_at,
               t.started_at,
               t.completed_at,
               (SELECT count(*)::int FROM task_results WHERE task_id = t.id) AS result_count,
               (SELECT count(*)::int FROM task_results
                  WHERE task_id = t.id AND error IS NOT NULL) AS error_count
        FROM tasks t
        {where_clause}
        ORDER BY t.created_at DESC, t.id DESC
        LIMIT ${len(params) + 1} OFFSET ${len(params) + 2}
    """  # noqa: S608
    count_query = f"SELECT count(*) FROM tasks t {where_clause}"  # noqa: S608

    list_params = [*params, limit, offset]
    async with pool.acquire() as conn:
        total = await conn.fetchval(count_query, *params)
        rows = await conn.fetch(list_query, *list_params)

    items = [AdminTaskSummary.model_validate(_row_to_summary(row)) for row in rows]
    return AdminTaskList(items=items, limit=limit, offset=offset, total=int(total or 0))


@router.get(
    "/{task_id}",
    response_model=AdminTaskDetail,
    summary="Fetch a task with its per-probe results (admin only).",
)
async def get_admin_task(
    pool: PoolDep,
    task_id: Annotated[int, Path(ge=1)],
) -> AdminTaskDetail:
    """Return a task and its results; 404 when the id is unknown."""
    async with pool.acquire() as conn:
        task_row = await conn.fetchrow(
            """
            SELECT id, link, status, probe_tokens, assigned_to,
                   created_at, started_at, completed_at
            FROM tasks WHERE id = $1
            """,
            task_id,
        )
        if task_row is None:
            raise HTTPException(status_code=404, detail="Task not found")
        result_rows = await conn.fetch(
            """
            SELECT tr.id, tr.task_id, tr.probe_id, p.alias AS probe_alias,
                   tr.region_id, r.code AS region_code, r.title AS region_title,
                   tr.speed_mbps, tr.error, tr.created_at
            FROM task_results tr
            JOIN probes p ON p.id = tr.probe_id
            JOIN regions r ON r.id = tr.region_id
            WHERE tr.task_id = $1
            ORDER BY tr.created_at ASC
            """,
            task_id,
        )
    results = [AdminTaskResultItem.model_validate(dict(row)) for row in result_rows]
    return AdminTaskDetail.model_validate(
        {
            "id": task_row["id"],
            "link": task_row["link"],
            "status": task_row["status"],
            "probe_tokens": list(task_row["probe_tokens"]) if task_row["probe_tokens"] else None,
            "assigned_to": task_row["assigned_to"],
            "created_at": task_row["created_at"],
            "started_at": task_row["started_at"],
            "completed_at": task_row["completed_at"],
            "results": [r.model_dump() for r in results],
        }
    )


def _row_to_summary(row: Any) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "link": str(row["link"]),
        "status": str(row["status"]),
        "probe_tokens": list(row["probe_tokens"]) if row["probe_tokens"] else None,
        "created_at": row["created_at"],
        "started_at": row["started_at"],
        "completed_at": row["completed_at"],
        "result_count": int(row["result_count"] or 0),
        "error_count": int(row["error_count"] or 0),
    }
