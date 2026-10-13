"""``/api/v1/regions`` endpoints.

Exposes a single ``GET /api/v1/regions`` route returning every region
with a few derived metrics used by the frontend map:

* ``probe_count`` — number of probes registered in the region
* ``last_median_mbps`` — median of the most recent speedtest results in
  the region (cap of 100 rows for hot regions)
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from xspeed_server.app.deps import PoolDep

router = APIRouter(prefix="/regions", tags=["regions"])

_REGION_STATS_QUERY = """
WITH recent AS (
    SELECT tr.region_id, tr.speed_mbps, tr.created_at
    FROM task_results tr
    WHERE tr.speed_mbps IS NOT NULL
      AND tr.created_at >= now() - interval '7 days'
),
ranked AS (
    SELECT region_id, speed_mbps,
           row_number() OVER (PARTITION BY region_id ORDER BY created_at DESC) AS rn
    FROM recent
),
top_per_region AS (
    SELECT region_id, speed_mbps
    FROM ranked
    WHERE rn <= 100
)
SELECT r.id,
       r.code,
       r.title,
       r.lat,
       r.lon,
       count(DISTINCT p.id) AS probe_count,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY tpr.speed_mbps) AS last_median_mbps
FROM regions r
LEFT JOIN probes p ON p.region_id = r.id
LEFT JOIN top_per_region tpr ON tpr.region_id = r.id
GROUP BY r.id, r.code, r.title, r.lat, r.lon
ORDER BY r.title
"""


class RegionOut(BaseModel):
    """One row in the regions list."""

    id: int
    code: str
    title: str
    lat: float | None
    lon: float | None
    probe_count: int
    last_median_mbps: float | None


class RegionList(BaseModel):
    """Envelope returned by ``GET /api/v1/regions``."""

    items: list[RegionOut]


def _row_to_payload(row: Any) -> dict[str, Any]:
    """Translate an asyncpg record into the JSON shape we want to expose."""
    return {
        "id": row["id"],
        "code": row["code"],
        "title": row["title"],
        "lat": row["lat"],
        "lon": row["lon"],
        "probe_count": int(row["probe_count"] or 0),
        "last_median_mbps": float(row["last_median_mbps"]) if row["last_median_mbps"] else None,
    }


@router.get(
    "",
    response_model=RegionList,
    summary="List regions with derived probe/speedtest statistics.",
)
async def list_regions(pool: PoolDep) -> RegionList:
    """Return every region plus its ``probe_count`` and ``last_median_mbps``."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(_REGION_STATS_QUERY)
    items = [RegionOut.model_validate(_row_to_payload(row)) for row in rows]
    return RegionList(items=items)


__all__ = ["router", "RegionList", "RegionOut"]
