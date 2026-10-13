"""``/api/v1/admin/probes`` endpoints (admin-protected CRUD).

The admin panel needs to list, create, update and delete probes
without having to talk SQL directly. Every route is guarded by
:func:`xspeed_server.app.core.dependencies.get_admin_user` so the
``Authorization: Bearer <jwt>`` check is the only entry barrier.

* ``GET  /api/v1/admin/probes`` — paginated list, newest first.
* ``POST /api/v1/admin/probes`` — create a probe; the auth token is
  generated server-side and returned in the response so the operator
  can hand it to the probe container.
* ``PUT  /api/v1/admin/probes/{id}`` — update alias/region/ip/is_public.
* ``DELETE /api/v1/admin/probes/{id}`` — drop a probe; ``404`` when
  the id is unknown, ``409`` when the probe still owns a
  ``task_results`` row (so the archive invariant survives).
"""

from __future__ import annotations

import secrets
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from pydantic import BaseModel, ConfigDict, Field, field_validator

from xspeed_server.app.core.dependencies import get_admin_user
from xspeed_server.app.deps import PoolDep

router = APIRouter(
    prefix="/admin/probes",
    tags=["admin-probes"],
    dependencies=[Depends(get_admin_user)],
)

__all__ = ["router", "ProbeOut", "ProbeList", "ProbeCreate", "ProbeUpdate"]


def _generate_token() -> str:
    """Return a 32-byte URL-safe random token for a new probe."""
    return secrets.token_urlsafe(32)


class ProbeCreate(BaseModel):
    """Payload accepted by ``POST /api/v1/admin/probes``."""

    model_config = ConfigDict(extra="forbid")

    alias: str = Field(default="", max_length=255)
    region_id: int = Field(..., ge=1, description="Owning region id.")
    is_public: bool = Field(default=True)
    token: str | None = Field(
        default=None,
        min_length=8,
        max_length=128,
        description="Optional operator-supplied token. Auto-generated when omitted.",
    )

    @field_validator("alias")
    @classmethod
    def _strip_alias(cls, value: str) -> str:
        return value.strip()


class ProbeUpdate(BaseModel):
    """Payload accepted by ``PUT /api/v1/admin/probes/{id}``."""

    model_config = ConfigDict(extra="forbid")

    alias: str | None = Field(default=None, max_length=255)
    region_id: int | None = Field(default=None, ge=1)
    is_public: bool | None = Field(default=None)
    token: str | None = Field(default=None, min_length=8, max_length=128)

    @field_validator("alias")
    @classmethod
    def _strip_alias(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip()


class ProbeOut(BaseModel):
    """One row in the admin probes list / detail view."""

    id: int
    alias: str
    token: str
    region_id: int
    region_code: str
    region_title: str
    ip: str | None
    is_public: bool
    last_seen_at: datetime | None
    created_at: datetime


class ProbeList(BaseModel):
    """Envelope returned by ``GET /api/v1/admin/probes``."""

    items: list[ProbeOut]
    limit: int
    offset: int
    total: int


def _row_to_payload(row: Any) -> dict[str, Any]:
    """Translate an asyncpg record into the JSON shape the admin panel wants."""
    return {
        "id": int(row["id"]),
        "alias": str(row["alias"] or ""),
        "token": str(row["token"]),
        "region_id": int(row["region_id"]),
        "region_code": str(row["region_code"]),
        "region_title": str(row["region_title"]),
        "ip": row["ip"],
        "is_public": bool(row["is_public"]),
        "last_seen_at": row["last_seen_at"],
        "created_at": row["created_at"],
    }


_SELECT_PROBES = """
SELECT p.id,
       p.alias,
       p.token,
       p.region_id,
       r.code AS region_code,
       r.title AS region_title,
       p.ip,
       p.is_public,
       p.last_seen_at,
       p.created_at
FROM probes p
JOIN regions r ON r.id = p.region_id
"""


@router.get(
    "",
    response_model=ProbeList,
    summary="List probes with their owning region (admin only).",
)
async def list_probes(
    pool: PoolDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ProbeList:
    """Return a page of probes ordered by ``created_at`` desc plus the total count."""
    async with pool.acquire() as conn:
        total = await conn.fetchval("SELECT count(*) FROM probes")
        rows = await conn.fetch(
            _SELECT_PROBES + " ORDER BY p.created_at DESC, p.id DESC LIMIT $1 OFFSET $2",
            limit,
            offset,
        )
    items = [ProbeOut.model_validate(_row_to_payload(row)) for row in rows]
    return ProbeList(items=items, limit=limit, offset=offset, total=int(total or 0))


@router.post(
    "",
    response_model=ProbeOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new probe (admin only).",
)
async def create_probe(payload: ProbeCreate, pool: PoolDep) -> ProbeOut:
    """Insert a probe and return it. Token is auto-generated when missing."""
    token = payload.token or _generate_token()
    async with pool.acquire() as conn:
        region_row = await conn.fetchrow(
            "SELECT id FROM regions WHERE id = $1",
            payload.region_id,
        )
        if region_row is None:
            raise HTTPException(status_code=404, detail="Region not found")
        try:
            row = await conn.fetchrow(
                """
                INSERT INTO probes (alias, token, region_id, is_public)
                VALUES ($1, $2, $3, $4)
                RETURNING id
                """,
                payload.alias,
                token,
                payload.region_id,
                payload.is_public,
            )
        except Exception as exc:  # noqa: BLE001 - translate to HTTP
            if _is_unique_violation(exc):
                raise HTTPException(status_code=409, detail="Probe token already exists") from exc
            raise
    if row is None:  # pragma: no cover - RETURNING is guaranteed by Postgres
        raise HTTPException(status_code=500, detail="Failed to create probe")
    probe_id = int(row["id"])
    return await _fetch_probe(pool, probe_id)


@router.put(
    "/{probe_id}",
    response_model=ProbeOut,
    summary="Update a probe (admin only).",
)
async def update_probe(
    payload: ProbeUpdate,
    pool: PoolDep,
    probe_id: Annotated[int, Path(ge=1)],
) -> ProbeOut:
    """Patch alias/region/ip/is_public/token; 404 when the id is unknown."""
    updates: dict[str, Any] = {}
    if payload.alias is not None:
        updates["alias"] = payload.alias
    if payload.region_id is not None:
        updates["region_id"] = payload.region_id
    if payload.is_public is not None:
        updates["is_public"] = payload.is_public
    if payload.token is not None:
        updates["token"] = payload.token

    if not updates:
        return await _fetch_probe(pool, probe_id)

    set_clauses: list[str] = []
    values: list[Any] = []
    for index, (column, value) in enumerate(updates.items(), start=1):
        set_clauses.append(f"{column} = ${index}")
        values.append(value)
    values.append(probe_id)
    update_sql = (
        f"UPDATE probes SET {', '.join(set_clauses)} "  # noqa: S608
        f"WHERE id = ${len(values)} RETURNING id"
    )

    async with pool.acquire() as conn:
        if "region_id" in updates:
            region_row = await conn.fetchrow(
                "SELECT id FROM regions WHERE id = $1",
                updates["region_id"],
            )
            if region_row is None:
                raise HTTPException(status_code=404, detail="Region not found")
        try:
            row = await conn.fetchrow(update_sql, *values)
        except Exception as exc:  # noqa: BLE001 - translate to HTTP
            if _is_unique_violation(exc):
                raise HTTPException(status_code=409, detail="Probe token already exists") from exc
            raise
    if row is None:
        raise HTTPException(status_code=404, detail="Probe not found")
    return await _fetch_probe(pool, probe_id)


@router.delete(
    "/{probe_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a probe (admin only).",
)
async def delete_probe(
    pool: PoolDep,
    probe_id: Annotated[int, Path(ge=1)],
) -> None:
    """Delete the probe; 404 if unknown, 409 if it still owns task_results."""
    async with pool.acquire() as conn:
        referenced = await conn.fetchval(
            "SELECT 1 FROM task_results WHERE probe_id = $1 LIMIT 1",
            probe_id,
        )
        if referenced is not None:
            raise HTTPException(
                status_code=409,
                detail="Probe has task_results rows; archive or clean up first",
            )
        row = await conn.fetchrow(
            "DELETE FROM probes WHERE id = $1 RETURNING id",
            probe_id,
        )
    if row is None:
        raise HTTPException(status_code=404, detail="Probe not found")


async def _fetch_probe(pool: Any, probe_id: int) -> ProbeOut:
    """Fetch a single probe with its region attached; 404 when missing."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            _SELECT_PROBES + " WHERE p.id = $1",
            probe_id,
        )
    if row is None:
        raise HTTPException(status_code=404, detail="Probe not found")
    return ProbeOut.model_validate(_row_to_payload(row))


def _is_unique_violation(exc: BaseException) -> bool:
    """Return True when ``exc`` is an asyncpg UNIQUE violation."""
    sqlstate = getattr(exc, "sqlstate", None)
    return sqlstate == "23505"
