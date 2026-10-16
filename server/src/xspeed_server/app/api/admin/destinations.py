"""``/api/v1/admin/destinations`` endpoints (admin-protected CRUD).

Operators populate a small catalogue of speedtest target URLs
through the admin panel. Each row exposes ``{id, name, url}`` to the
public ``/api/v1/destinations`` endpoint so the homepage dropdown can
show the available choices.

* ``GET    /api/v1/admin/destinations`` — paginated list, newest first.
* ``POST   /api/v1/admin/destinations`` — create a destination.
* ``PUT    /api/v1/admin/destinations/{id}`` — patch name/url.
* ``DELETE /api/v1/admin/destinations/{id}`` — drop a destination;
  ``404`` when the id is unknown, ``409`` when an existing task still
  references it (so history is preserved).
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from pydantic import BaseModel, ConfigDict, Field, field_validator

from xspeed_server.app.core.dependencies import get_admin_user
from xspeed_server.app.deps import PoolDep

router = APIRouter(
    prefix="/admin/destinations",
    tags=["admin-destinations"],
    dependencies=[Depends(get_admin_user)],
)

__all__ = [
    "router",
    "DestinationCreate",
    "DestinationUpdate",
    "DestinationOut",
    "DestinationList",
]


class DestinationCreate(BaseModel):
    """Payload accepted by ``POST /api/v1/admin/destinations``."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=128)
    url: str = Field(
        ...,
        min_length=1,
        max_length=2048,
        pattern=r"^https?://.+$",
        description="Absolute http(s) URL of the speedtest target.",
    )

    @field_validator("name")
    @classmethod
    def _strip_name(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("name must not be empty")
        return cleaned


class DestinationUpdate(BaseModel):
    """Payload accepted by ``PUT /api/v1/admin/destinations/{id}``."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=128)
    url: str | None = Field(default=None, min_length=1, max_length=2048, pattern=r"^https?://.+$")

    @field_validator("name")
    @classmethod
    def _strip_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("name must not be empty")
        return cleaned


class DestinationOut(BaseModel):
    """One row in the admin destinations list / detail view."""

    id: int
    name: str
    url: str
    created_at: datetime


class DestinationList(BaseModel):
    """Envelope returned by ``GET /api/v1/admin/destinations``."""

    items: list[DestinationOut]
    limit: int
    offset: int
    total: int


_SELECT_DESTINATIONS = """
SELECT id, name, url, created_at
FROM destinations
"""


def _row_to_payload(row: Any) -> dict[str, Any]:
    """Translate an asyncpg record into the JSON shape the admin panel wants."""
    return {
        "id": int(row["id"]),
        "name": str(row["name"]),
        "url": str(row["url"]),
        "created_at": row["created_at"],
    }


@router.get(
    "",
    response_model=DestinationList,
    summary="List destinations (admin only).",
)
async def list_destinations(
    pool: PoolDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> DestinationList:
    """Return a page of destinations ordered by ``created_at`` desc plus the total count."""
    async with pool.acquire() as conn:
        total = await conn.fetchval("SELECT count(*) FROM destinations")
        rows = await conn.fetch(
            _SELECT_DESTINATIONS + " ORDER BY created_at DESC, id DESC LIMIT $1 OFFSET $2",
            limit,
            offset,
        )
    items = [DestinationOut.model_validate(_row_to_payload(row)) for row in rows]
    return DestinationList(items=items, limit=limit, offset=offset, total=int(total or 0))


@router.post(
    "",
    response_model=DestinationOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a destination (admin only).",
)
async def create_destination(
    payload: DestinationCreate,
    pool: PoolDep,
) -> DestinationOut:
    """Insert a destination and return it."""
    async with pool.acquire() as conn:
        try:
            row = await conn.fetchrow(
                """
                INSERT INTO destinations (name, url)
                VALUES ($1, $2)
                RETURNING id
                """,
                payload.name,
                payload.url,
            )
        except Exception as exc:  # noqa: BLE001 - translate to HTTP
            if _is_unique_violation(exc):
                raise HTTPException(
                    status_code=409,
                    detail="Destination name already exists",
                ) from exc
            raise
    if row is None:  # pragma: no cover - RETURNING is guaranteed by Postgres
        raise HTTPException(status_code=500, detail="Failed to create destination")
    destination_id = int(row["id"])
    return await _fetch_destination(pool, destination_id)


@router.put(
    "/{destination_id}",
    response_model=DestinationOut,
    summary="Update a destination (admin only).",
)
async def update_destination(
    payload: DestinationUpdate,
    pool: PoolDep,
    destination_id: Annotated[int, Path(ge=1)],
) -> DestinationOut:
    """Patch name/url; 404 when the id is unknown."""
    updates: dict[str, Any] = {}
    if payload.name is not None:
        updates["name"] = payload.name
    if payload.url is not None:
        updates["url"] = payload.url

    if not updates:
        return await _fetch_destination(pool, destination_id)

    set_clauses: list[str] = []
    values: list[Any] = []
    for index, (column, value) in enumerate(updates.items(), start=1):
        set_clauses.append(f"{column} = ${index}")
        values.append(value)
    values.append(destination_id)
    update_sql = (
        f"UPDATE destinations SET {', '.join(set_clauses)} "  # noqa: S608
        f"WHERE id = ${len(values)} RETURNING id"
    )

    async with pool.acquire() as conn:
        try:
            row = await conn.fetchrow(update_sql, *values)
        except Exception as exc:  # noqa: BLE001 - translate to HTTP
            if _is_unique_violation(exc):
                raise HTTPException(
                    status_code=409,
                    detail="Destination name already exists",
                ) from exc
            raise
    if row is None:
        raise HTTPException(status_code=404, detail="Destination not found")
    return await _fetch_destination(pool, destination_id)


@router.delete(
    "/{destination_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a destination (admin only).",
)
async def delete_destination(
    pool: PoolDep,
    destination_id: Annotated[int, Path(ge=1)],
) -> None:
    """Delete the destination; 404 if unknown, 409 if a task still references it."""
    async with pool.acquire() as conn:
        referenced = await conn.fetchval(
            "SELECT 1 FROM tasks WHERE destination_id = $1 LIMIT 1",
            destination_id,
        )
        if referenced is not None:
            raise HTTPException(
                status_code=409,
                detail="Destination is referenced by existing tasks; clear them first",
            )
        row = await conn.fetchrow(
            "DELETE FROM destinations WHERE id = $1 RETURNING id",
            destination_id,
        )
    if row is None:
        raise HTTPException(status_code=404, detail="Destination not found")


async def _fetch_destination(pool: Any, destination_id: int) -> DestinationOut:
    """Fetch a single destination; 404 when missing."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            _SELECT_DESTINATIONS + " WHERE id = $1",
            destination_id,
        )
    if row is None:
        raise HTTPException(status_code=404, detail="Destination not found")
    return DestinationOut.model_validate(_row_to_payload(row))


def _is_unique_violation(exc: BaseException) -> bool:
    """Return True when ``exc`` is an asyncpg UNIQUE violation."""
    sqlstate = getattr(exc, "sqlstate", None)
    return sqlstate == "23505"
