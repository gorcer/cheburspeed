"""``/api/v1/tasks`` endpoints.

Exposes:

* ``POST /api/v1/tasks`` — enqueue a new speedtest task (rate-limited per
  source IP, quota comes from ``rate_limit_task_create_per_hour``).
* ``GET  /api/v1/tasks/{task_id}`` — fetch a single task with its
  per-probe results (quota: ``rate_limit_task_read_per_minute``).
* ``GET  /api/v1/tasks`` — paginated task list, newest first (quota:
  ``rate_limit_task_list_per_minute``).
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Annotated, Any
from urllib.parse import urlparse

from asyncpg import Connection
from fastapi import APIRouter, HTTPException, Path, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator

from xspeed_server.app.core.ratelimit import limit_provider, limiter
from xspeed_server.app.deps import PoolDep
from xspeed_server.app.ws.protocol import TaskProtocol

router = APIRouter(prefix="/tasks", tags=["tasks"])

VLESS_LINK_PATTERN = r"^vless://[0-9a-f-]{36}@[\w.-]+:\d+\?.*$"

SUPPORTED_PROTOCOLS: tuple[TaskProtocol, ...] = ("vless", "amneziawg")

MAX_LINK_LENGTH = 8192

AWG_REQUIRED_INTERFACE_KEYS = ("PrivateKey", "Address")
AWG_REQUIRED_PEER_KEYS = ("PublicKey", "Endpoint")


def _validate_awg_link(link: str) -> str:
    """Return ``link`` when it looks like an AmneziaWG INI config.

    The check is deliberately structural rather than a full parser: the
    probe owns the authoritative parsing (``xspeed_probe.awg``) and
    rejects anything it cannot dial. Here we only reject payloads the
    probe could never use, so the API fails fast on an empty textarea
    or a pasted ``vless://`` URI with the protocol set to ``amneziawg``.
    """
    sections: dict[str, set[str]] = {}
    current: str | None = None
    for raw_line in link.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw_line.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if line.startswith("[") and line.endswith("]"):
            current = line[1:-1].strip().lower()
            sections.setdefault(current, set())
            continue
        if current is None or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if value.strip():
            sections[current].add(key.strip().lower())
    interface = sections.get("interface", set())
    peer = sections.get("peer", set())
    missing = [
        name
        for name in AWG_REQUIRED_INTERFACE_KEYS + AWG_REQUIRED_PEER_KEYS
        if name.lower() not in interface and name.lower() not in peer
    ]
    if not interface or not peer:
        missing.append("[Interface]/[Peer]")
    if missing:
        raise ValueError(
            "amneziawg link must be an INI config with [Interface] "
            "(PrivateKey, Address) and [Peer] (PublicKey, Endpoint); missing: "
            + ", ".join(missing),
        )
    return link


class TaskCreate(BaseModel):
    """Payload accepted by ``POST /api/v1/tasks``."""

    model_config = ConfigDict(extra="forbid")

    protocol: TaskProtocol = Field(
        default="vless",
        description=(
            "Tunnel protocol the ``link`` field carries. ``vless`` expects a "
            "``vless://`` URI, ``amneziawg`` expects the official AmneziaVPN "
            "INI config (``[Interface]``/``[Peer]``)."
        ),
    )
    link: str = Field(
        ...,
        min_length=1,
        max_length=MAX_LINK_LENGTH,
        description=(
            "vless:// URI when protocol is ``vless``; a full AmneziaWG INI "
            "config when protocol is ``amneziawg``."
        ),
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

    @field_validator("link")
    @classmethod
    def _check_link(cls, value: str, info: ValidationInfo) -> str:
        """Validate ``link`` against the declared ``protocol``.

        Declared before ``link`` so ``info.data`` already holds it; this
        keeps the reported error location at ``["body", "link"]`` — the
        same shape callers already handle.
        """
        protocol = info.data.get("protocol", "vless")
        if protocol == "amneziawg":
            _validate_awg_link(value)
            return value
        if not re.fullmatch(VLESS_LINK_PATTERN, value):
            raise ValueError("link must be a vless:// URI when protocol is 'vless'")
        return value

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
    protocol: TaskProtocol
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
    protocol: TaskProtocol
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
    protocol: TaskProtocol
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
    protocol: str,
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
            INSERT INTO tasks (link, protocol, destination_id, status, probe_tokens, created_at)
            VALUES ($1, $2, $3, 'pending', $4::jsonb, now())
            RETURNING id
            """,
            link,
            protocol,
            destination_id,
            None if probe_tokens is None else json.dumps(probe_tokens),
        )
    except Exception as exc:  # noqa: BLE001 - translate FK violation to 404
        if _is_foreign_key_violation(exc):
            raise HTTPException(status_code=404, detail="Not found") from exc
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
        SELECT id, link, protocol, status, probe_tokens, destination_id, created_at, completed_at
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
    protocol = str(task_row["protocol"])
    return {
        "id": task_row["id"],
        "link": task_row["link"],
        "protocol": protocol,
        "address": _extract_address(str(task_row["link"]), protocol),
        "status": task_row["status"],
        "probe_tokens": list(task_row["probe_tokens"]) if task_row["probe_tokens"] else None,
        "destination_id": destination_id,
        "destination_name": destination[0] if destination else None,
        "destination_url": destination[1] if destination else None,
        "created_at": task_row["created_at"],
        "completed_at": task_row["completed_at"],
        "results": [dict(row) for row in result_rows],
    }


def _extract_address(link: str, protocol: str) -> str | None:
    """Return ``host[:port]`` of the tunnel endpoint, or ``None`` on failure.

    For ``vless`` the URI shape is ``vless://<UUID>@<host>:<port>?<query>#<name>``;
    :func:`urllib.parse.urlparse` already handles the userinfo split so
    :attr:`ParseResult.hostname` and :attr:`ParseResult.port` are exactly
    what we need to surface in the UI. We bracket an IPv6 host (no port
    is exposed by ``urlparse`` in that case but the operator can still
    see the address).

    For ``amneziawg`` the endpoint lives in the ``[Peer]`` section of the
    INI config as ``Endpoint = host:port``; the same split is applied so
    the "Адрес" column keeps working for both protocols.
    """
    if protocol == "amneziawg":
        return _extract_awg_endpoint(link)
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


def _extract_awg_endpoint(link: str) -> str | None:
    """Return the ``Endpoint`` value of the first ``[Peer]`` section."""
    in_peer = False
    for raw_line in link.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw_line.strip()
        if line.startswith("[") and line.endswith("]"):
            in_peer = line[1:-1].strip().lower() == "peer"
            continue
        if not in_peer or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip().lower() == "endpoint" and value.strip():
            return value.strip()
    return None


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
@limiter.limit(limit_provider(lambda settings: settings.rate_limit_task_create_per_hour, "hour"))
async def create_task(
    request: Request,  # required by slowapi to extract the client IP
    payload: TaskCreate,
    pool: PoolDep,
) -> TaskCreatedResponse:
    """Validate the tunnel link, insert a ``pending`` row and return its id."""
    async with pool.acquire() as conn:
        destination = await _resolve_destination(conn, payload.destination_id)
        if destination is None:
            raise HTTPException(status_code=404, detail="Not found")
        task_id = await _insert_task(
            conn,
            payload.link,
            payload.protocol,
            payload.destination_id,
            payload.probe_tokens,
        )
        destination_name, destination_url = destination
    hub = request.app.state.hub
    await hub.broadcast({"type": "task.created", "payload": {"id": task_id, "status": "pending"}})
    return TaskCreatedResponse(
        id=task_id,
        status="pending",
        protocol=payload.protocol,
        destination_id=payload.destination_id,
        destination_url=destination_url,
    )


@router.get(
    "/{task_id}",
    response_model=TaskDetail,
    summary="Fetch a task along with its per-probe results.",
)
@limiter.limit(limit_provider(lambda settings: settings.rate_limit_task_read_per_minute, "minute"))
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
@limiter.limit(limit_provider(lambda settings: settings.rate_limit_task_list_per_minute, "minute"))
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
            SELECT t.id, t.link, t.protocol, t.status, t.destination_id,
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
    protocol = str(row["protocol"])
    return {
        "id": int(row["id"]),
        "link": str(row["link"]),
        "protocol": protocol,
        "address": _extract_address(str(row["link"]), protocol),
        "status": str(row["status"]),
        "destination_id": row["destination_id"],
        "destination_name": row["destination_name"],
        "destination_url": row["destination_url"],
        "created_at": row["created_at"],
        "completed_at": row["completed_at"],
    }


__all__ = [
    "router",
    "SUPPORTED_PROTOCOLS",
    "TaskCreate",
    "TaskCreatedResponse",
    "TaskDetail",
    "TaskList",
    "TaskProtocol",
    "TaskSummary",
    "TaskResultItem",
    "VLESS_LINK_PATTERN",
]
