"""``/api/v1/destinations`` endpoint.

Public catalogue of speedtest target URLs. The homepage dropdown
fetches this list once on mount and refuses to submit until the
operator picks one (or the list is empty — in which case the form
disables itself with a hint to add a destination in the admin panel).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict

from xspeed_server.app.deps import PoolDep

router = APIRouter(prefix="/destinations", tags=["destinations"])

__all__ = ["router", "DestinationPublic", "DestinationList"]


class DestinationPublic(BaseModel):
    """One row in the public destinations list."""

    id: int
    name: str
    url: str


class DestinationList(BaseModel):
    """Envelope returned by ``GET /api/v1/destinations``."""

    model_config = ConfigDict(extra="forbid")

    items: list[DestinationPublic]


_SELECT_DESTINATIONS = """
SELECT id, name, url
FROM destinations
ORDER BY name ASC, id ASC
"""


def _row_to_payload(row: Any) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "name": str(row["name"]),
        "url": str(row["url"]),
    }


@router.get(
    "",
    response_model=DestinationList,
    summary="List speedtest target URLs for the homepage form.",
)
async def list_destinations(pool: PoolDep) -> DestinationList:
    """Return every destination ordered alphabetically by name.

    The catalogue is expected to stay short (single-digit rows); the
    homepage fetches it once on mount, so no pagination is exposed.
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(_SELECT_DESTINATIONS)
    items = [DestinationPublic.model_validate(_row_to_payload(row)) for row in rows]
    return DestinationList(items=items)
