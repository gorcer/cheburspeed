"""``/api/v1/probes`` endpoints.

Exposes a single ``GET /api/v1/probes/online`` route returning, for
each region, the count of currently-online probes, the median speed
from the most recent results and the timestamp of the last speedtest
recorded in the region. The map view on the frontend consumes this
payload to colour each region square.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from xspeed_server.app.deps import PoolDep, SettingsDep

router = APIRouter(prefix="/probes", tags=["probes"])


_ONLINE_PROBES_QUERY = """
WITH online AS (
    SELECT region_id, count(*) AS online_count
    FROM probes
    WHERE last_seen_at IS NOT NULL
      AND last_seen_at >= now() - ($1 || ' seconds')::interval
    GROUP BY region_id
),
recent AS (
    SELECT region_id, speed_mbps, created_at
    FROM task_results
    WHERE speed_mbps IS NOT NULL
      AND created_at >= now() - interval '7 days'
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
),
median_per_region AS (
    SELECT region_id, percentile_cont(0.5) WITHIN GROUP (ORDER BY speed_mbps) AS median_mbps
    FROM top_per_region
    GROUP BY region_id
),
last_speedtest AS (
    SELECT region_id, max(created_at) AS last_speedtest_at
    FROM task_results
    GROUP BY region_id
)
SELECT r.code,
       coalesce(o.online_count, 0) AS online,
       mpr.median_mbps,
       ls.last_speedtest_at
FROM regions r
LEFT JOIN online o ON o.region_id = r.id
LEFT JOIN median_per_region mpr ON mpr.region_id = r.id
LEFT JOIN last_speedtest ls ON ls.region_id = r.id
"""


class OnlineRegion(BaseModel):
    """One row in the ``/probes/online`` payload."""

    online: int
    median_mbps: float | None
    last_speedtest_at: datetime | None


class OnlineProbesResponse(BaseModel):
    """Envelope returned by ``GET /api/v1/probes/online``."""

    regions: dict[str, OnlineRegion]


def _row_to_payload(row: Any) -> dict[str, Any]:
    return {
        "online": int(row["online"] or 0),
        "median_mbps": float(row["median_mbps"]) if row["median_mbps"] else None,
        "last_speedtest_at": row["last_speedtest_at"],
    }


@router.get(
    "/online",
    response_model=OnlineProbesResponse,
    summary="Per-region online probe count, median speed and last speedtest time.",
)
async def get_online_probes(
    pool: PoolDep,
    settings: SettingsDep,
) -> OnlineProbesResponse:
    """Return the per-region summary used to colour the frontend map."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(_ONLINE_PROBES_QUERY, str(settings.probe_online_after_seconds))
    regions = {row["code"]: OnlineRegion.model_validate(_row_to_payload(row)) for row in rows}
    return OnlineProbesResponse(regions=regions)


__all__ = ["router", "OnlineProbesResponse", "OnlineRegion"]
