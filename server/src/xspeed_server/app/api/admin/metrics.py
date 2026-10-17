"""``/api/v1/admin/metrics`` endpoints (admin-protected journal).

Operators populate the ``metrics`` table — speed, load and other
client-side samples — through the admin panel. Each row exposes
``{id, client_ip, client_isp, client_lat, client_lng, server_id, ip,
metric_alias, value, created_at}`` and is joined with ``probes`` /
``regions`` so the UI can render the journal without a second round
trip.

* ``GET  /api/v1/admin/metrics`` — paginated list, newest first.
* ``POST /api/v1/admin/metrics`` — append a sample; rejects unknown
  ``server_id`` with ``404 Server not found``.

The journal is intentionally write-friendly (no delete/update) because
the rows are immutable telemetry — corrections should land as new
rows so the history stays auditable.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from pydantic import BaseModel, ConfigDict, Field, field_validator

from xspeed_server.app.core.dependencies import get_admin_user
from xspeed_server.app.deps import PoolDep

router = APIRouter(
    prefix="/admin/metrics",
    tags=["admin-metrics"],
    dependencies=[Depends(get_admin_user)],
)

__all__ = [
    "router",
    "MetricCreate",
    "MetricOut",
    "MetricList",
]


class MetricCreate(BaseModel):
    """Payload accepted by ``POST /api/v1/admin/metrics``."""

    model_config = ConfigDict(extra="forbid")

    client_ip: str = Field(..., min_length=1, max_length=64)
    client_isp: str | None = Field(default=None, max_length=255)
    client_lat: float | None = Field(default=None, ge=-90.0, le=90.0)
    client_lng: float | None = Field(default=None, ge=-180.0, le=180.0)
    server_id: int = Field(..., ge=1, description="Owning probe id.")
    ip: str | None = Field(default=None, max_length=64)
    metric_alias: str = Field(..., min_length=1, max_length=128)
    value: float | None = Field(default=None)

    @field_validator("client_ip")
    @classmethod
    def _strip_client_ip(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("client_ip must not be empty")
        return cleaned

    @field_validator("client_isp")
    @classmethod
    def _strip_client_isp(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None

    @field_validator("ip")
    @classmethod
    def _strip_ip(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None

    @field_validator("metric_alias")
    @classmethod
    def _strip_metric_alias(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("metric_alias must not be empty")
        return cleaned


class MetricOut(BaseModel):
    """One row in the admin metrics journal."""

    id: int
    client_ip: str
    client_isp: str | None
    client_lat: float | None
    client_lng: float | None
    server_id: int
    server_alias: str
    region_code: str
    region_title: str
    ip: str | None
    metric_alias: str
    value: float | None
    created_at: datetime


class MetricList(BaseModel):
    """Envelope returned by ``GET /api/v1/admin/metrics``."""

    items: list[MetricOut]
    limit: int
    offset: int
    total: int


_SELECT_METRICS = """
SELECT m.id,
       m.client_ip,
       m.client_isp,
       m.client_lat,
       m.client_lng,
       m.server_id,
       p.alias AS server_alias,
       r.code AS region_code,
       r.title AS region_title,
       m.ip,
       m.metric_alias,
       m.value,
       m.created_at
FROM metrics m
JOIN probes p ON p.id = m.server_id
JOIN regions r ON r.id = p.region_id
"""


def _row_to_payload(row: Any) -> dict[str, Any]:
    """Translate an asyncpg record into the JSON shape the admin panel wants."""
    return {
        "id": int(row["id"]),
        "client_ip": str(row["client_ip"]),
        "client_isp": row["client_isp"],
        "client_lat": row["client_lat"],
        "client_lng": row["client_lng"],
        "server_id": int(row["server_id"]),
        "server_alias": str(row["server_alias"] or ""),
        "region_code": str(row["region_code"]),
        "region_title": str(row["region_title"]),
        "ip": row["ip"],
        "metric_alias": str(row["metric_alias"]),
        "value": row["value"],
        "created_at": row["created_at"],
    }


@router.get(
    "",
    response_model=MetricList,
    summary="List metric samples (admin only).",
)
async def list_metrics(
    pool: PoolDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> MetricList:
    """Return a page of metrics ordered by ``created_at`` desc plus the total count."""
    async with pool.acquire() as conn:
        total = await conn.fetchval("SELECT count(*) FROM metrics")
        rows = await conn.fetch(
            _SELECT_METRICS + " ORDER BY m.created_at DESC, m.id DESC LIMIT $1 OFFSET $2",
            limit,
            offset,
        )
    items = [MetricOut.model_validate(_row_to_payload(row)) for row in rows]
    return MetricList(items=items, limit=limit, offset=offset, total=int(total or 0))


@router.post(
    "",
    response_model=MetricOut,
    status_code=status.HTTP_201_CREATED,
    summary="Record a new metric sample (admin only).",
)
async def create_metric(payload: MetricCreate, pool: PoolDep) -> MetricOut:
    """Insert a metric row and return the joined journal entry.

    A missing ``server_id`` is reported as ``404 Server not found`` so
    the client can distinguish between "invalid probe" and "server
    temporarily unavailable". The check is a small extra round trip but
    keeps the error message human-friendly; the FK constraint would
    catch it later anyway, but asyncpg surfaces that as ``sqlstate 23503``
    which we translate in :func:`_is_foreign_key_violation` so the
    fallback path matches ``POST /api/v1/tasks``.
    """
    async with pool.acquire() as conn:
        probe_row = await conn.fetchrow(
            "SELECT id FROM probes WHERE id = $1",
            payload.server_id,
        )
        if probe_row is None:
            raise HTTPException(status_code=404, detail="Server not found")
        try:
            row = await conn.fetchrow(
                """
                INSERT INTO metrics (
                    client_ip, client_isp, client_lat, client_lng,
                    server_id, ip, metric_alias, value
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                RETURNING id
                """,
                payload.client_ip,
                payload.client_isp,
                payload.client_lat,
                payload.client_lng,
                payload.server_id,
                payload.ip,
                payload.metric_alias,
                payload.value,
            )
        except Exception as exc:  # noqa: BLE001 - translate FK violation to 404
            if _is_foreign_key_violation(exc):
                raise HTTPException(status_code=404, detail="Server not found") from exc
            raise
    if row is None:  # pragma: no cover - RETURNING is guaranteed by Postgres
        raise HTTPException(status_code=500, detail="Failed to create metric")
    metric_id = int(row["id"])
    return await _fetch_metric(pool, metric_id)


@router.get(
    "/{metric_id}",
    response_model=MetricOut,
    summary="Fetch a single metric sample (admin only).",
)
async def get_metric(
    pool: PoolDep,
    metric_id: Annotated[int, Path(ge=1)],
) -> MetricOut:
    """Return one metric row; ``404`` when the id is unknown."""
    return await _fetch_metric(pool, metric_id)


async def _fetch_metric(pool: Any, metric_id: int) -> MetricOut:
    """Fetch a single metric joined with its probe and region; 404 when missing."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(_SELECT_METRICS + " WHERE m.id = $1", metric_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Metric not found")
    return MetricOut.model_validate(_row_to_payload(row))


def _is_foreign_key_violation(exc: BaseException) -> bool:
    """Return True when ``exc`` is an asyncpg foreign-key violation."""
    sqlstate = getattr(exc, "sqlstate", None)
    return sqlstate == "23503"
