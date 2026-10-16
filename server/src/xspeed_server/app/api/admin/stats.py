"""``/api/v1/admin/stats`` endpoint (admin-protected aggregate).

Returns the data the dashboard needs at a glance:

* ``median_per_region_7d`` — median speed per region, computed over
  the last 7 days of ``task_results`` (top 100 results per region to
  bound the working set).
* ``histogram`` — bucketed count of ``task_results`` rows by speed
  range for the last 7 days. The buckets are stable so the frontend
  can render a column chart with a fixed x-axis.
* ``archive_lag_seconds`` — freshness of the cold-storage archive
  job (``now() - MAX(archived_at)``).
* ``totals`` — global counts for tasks, probes, regions, results.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict

from xspeed_server.app.core.dependencies import get_admin_user
from xspeed_server.app.deps import PoolDep

router = APIRouter(
    prefix="/admin/stats",
    tags=["admin-stats"],
    dependencies=[Depends(get_admin_user)],
)

__all__ = ["router", "RegionMedian", "AdminStats", "HistogramBucket"]


class RegionMedian(BaseModel):
    """One row in the per-region median list."""

    region_id: int
    region_code: str
    region_title: str
    median_mbps: float | None
    sample_count: int


class HistogramBucket(BaseModel):
    """A single speed bucket in the ``histogram`` array."""

    bucket: str
    min_mbps: float
    max_mbps: float | None
    count: int


class Totals(BaseModel):
    """Global counters for the dashboard tiles."""

    regions: int
    probes: int
    tasks: int
    results_24h: int
    results_7d: int


class AdminStats(BaseModel):
    """Response returned by ``GET /api/v1/admin/stats``."""

    model_config = ConfigDict(extra="forbid")

    median_per_region_7d: list[RegionMedian]
    histogram: list[HistogramBucket]
    archive_lag_seconds: float | None
    totals: Totals
    generated_at: datetime


_MEDIAN_QUERY = """
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
SELECT r.id AS region_id, r.code, r.title,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY tpr.speed_mbps) AS median_mbps,
       count(tpr.speed_mbps)::int AS sample_count
FROM regions r
LEFT JOIN top_per_region tpr ON tpr.region_id = r.id
GROUP BY r.id, r.code, r.title
ORDER BY r.title
"""


_HISTOGRAM_BUCKETS: list[tuple[str, float, float | None]] = [
    ("lt_1", 0.0, 1.0),
    ("1_5", 1.0, 5.0),
    ("5_20", 5.0, 20.0),
    ("20_50", 20.0, 50.0),
    ("50_100", 50.0, 100.0),
    ("gte_100", 100.0, None),
]


_HISTOGRAM_QUERY_TEMPLATE = """
SELECT count(*)::int AS c
FROM task_results
WHERE speed_mbps IS NOT NULL
  AND created_at >= now() - interval '7 days'
  AND speed_mbps >= $1
  AND ($2::numeric IS NULL OR speed_mbps < $2)
"""


_TOTALS_QUERY = """
SELECT
    (SELECT count(*) FROM regions) AS regions,
    (SELECT count(*) FROM probes) AS probes,
    (SELECT count(*) FROM tasks) AS tasks,
    (SELECT count(*) FROM task_results
       WHERE created_at >= now() - interval '24 hours') AS results_24h,
    (SELECT count(*) FROM task_results
       WHERE created_at >= now() - interval '7 days') AS results_7d
"""


_LAG_QUERY = """
SELECT
    EXTRACT(EPOCH FROM (now() - MAX(archived_at)))::double precision AS lag_seconds
FROM task_results_archive
"""


@router.get(
    "",
    response_model=AdminStats,
    summary="Aggregate dashboard data (admin only).",
)
async def get_admin_stats(pool: PoolDep) -> AdminStats:
    """Return per-region medians, the speed histogram, archive lag and totals."""
    async with pool.acquire() as conn:
        median_rows = await conn.fetch(_MEDIAN_QUERY)
        histogram_rows: list[HistogramBucket] = []
        for label, lo, hi in _HISTOGRAM_BUCKETS:
            count = int(await conn.fetchval(_HISTOGRAM_QUERY_TEMPLATE, lo, hi) or 0)
            histogram_rows.append(
                HistogramBucket(bucket=label, min_mbps=lo, max_mbps=hi, count=count)
            )
        totals_row = await conn.fetchrow(_TOTALS_QUERY)
        lag_value = await conn.fetchval(_LAG_QUERY)
    medians = [
        RegionMedian(
            region_id=int(row["region_id"]),
            region_code=str(row["code"]),
            region_title=str(row["title"]),
            median_mbps=float(row["median_mbps"]) if row["median_mbps"] else None,
            sample_count=int(row["sample_count"] or 0),
        )
        for row in median_rows
    ]
    totals = Totals(
        regions=int(totals_row["regions"] or 0),
        probes=int(totals_row["probes"] or 0),
        tasks=int(totals_row["tasks"] or 0),
        results_24h=int(totals_row["results_24h"] or 0),
        results_7d=int(totals_row["results_7d"] or 0),
    )
    lag = float(lag_value) if lag_value is not None else None
    return AdminStats(
        median_per_region_7d=medians,
        histogram=histogram_rows,
        archive_lag_seconds=lag,
        totals=totals,
        generated_at=datetime.now(UTC),
    )
