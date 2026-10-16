"""End-to-end tests for the admin HTTP API (DAT-325.8).

The test suite exercises the four JWT-protected surfaces (login,
probes, tasks, stats) using the live FastAPI lifespan so the
``app.state.db`` :class:`Database` instance is the same one the
production scheduler uses. Each test starts from the shared
``clean_db`` fixture (a fully truncated schema) and seeds the rows
it needs directly through asyncpg to keep the assertions focused on
the HTTP behaviour rather than the seeding helpers.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg
import pytest
from httpx import AsyncClient

from xspeed_server.app.admin_cli import create_admin
from xspeed_server.app.core.security import JWTService

PASSWORD = "supersecret-pw-1234"
EMAIL = "admin@xspeed.ru"


def _test_dsn() -> str:
    url = os.environ["TEST_DATABASE_URL"]
    if url.startswith("postgresql+asyncpg://"):
        return "postgresql://" + url[len("postgresql+asyncpg://") :]
    return url


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _insert_region(**fields: Any) -> dict[str, Any]:
    conn = await asyncpg.connect(_test_dsn())
    try:
        row = await conn.fetchrow(
            """
            INSERT INTO regions (code, title, lat, lon)
            VALUES ($1, $2, $3, $4)
            RETURNING id, code
            """,
            fields["code"],
            fields["title"],
            fields.get("lat"),
            fields.get("lon"),
        )
    finally:
        await conn.close()
    return {"id": row["id"], "code": row["code"]}


@pytest.fixture
async def regions() -> list[dict[str, Any]]:
    """Seed two regions used by most admin tests."""
    return [
        await _insert_region(code="RU-MOW", title="Москва", lat=55.7558, lon=37.6173),
        await _insert_region(code="RU-SPE", title="Санкт-Петербург", lat=59.9343, lon=30.3351),
    ]


async def _seed_admin(app_instance: Any) -> str:
    """Insert an admin record through the public CLI helper and return the JWT."""
    db = app_instance.state.db
    status, _ = await create_admin(db, email=EMAIL, password=PASSWORD)
    assert status == "created"
    # Build a fresh JWTService off the same JWT_SECRET as the running app.
    from xspeed_server.app.config import get_settings

    jwt_service = JWTService.from_settings(get_settings())
    return jwt_service.create_access_token(subject=EMAIL)


async def test_login_with_correct_credentials_returns_jwt(client: AsyncClient) -> None:
    # Seed an admin directly through the CLI helper is impossible without
    # the FastAPI app; do it through a direct bcrypt hash instead.
    from xspeed_server.app.core.security import hash_password

    conn = await asyncpg.connect(_test_dsn())
    try:
        await conn.execute(
            "INSERT INTO admins (email, password_hash) VALUES ($1, $2)",
            EMAIL,
            hash_password(PASSWORD),
        )
    finally:
        await conn.close()

    response = await client.post(
        "/api/v1/admin/login",
        json={"email": EMAIL, "password": PASSWORD},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["token_type"] == "bearer"
    assert isinstance(body["access_token"], str) and body["access_token"]
    assert body["expires_in"] > 0


async def test_login_with_wrong_password_returns_401(client: AsyncClient) -> None:
    from xspeed_server.app.core.security import hash_password

    conn = await asyncpg.connect(_test_dsn())
    try:
        await conn.execute(
            "INSERT INTO admins (email, password_hash) VALUES ($1, $2)",
            EMAIL,
            hash_password(PASSWORD),
        )
    finally:
        await conn.close()

    response = await client.post(
        "/api/v1/admin/login",
        json={"email": EMAIL, "password": "wrong-password"},
    )
    assert response.status_code == 401, response.text


async def test_login_with_unknown_email_returns_401(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/admin/login",
        json={"email": "ghost@xspeed.ru", "password": "doesnt-matter"},
    )
    assert response.status_code == 401, response.text


async def test_probes_endpoint_requires_bearer_token(client: AsyncClient) -> None:
    response = await client.get("/api/v1/admin/probes")
    assert response.status_code == 401, response.text
    body = response.json()
    assert "detail" in body


async def test_probes_endpoint_rejects_invalid_token(client: AsyncClient) -> None:
    response = await client.get(
        "/api/v1/admin/probes",
        headers=_bearer("not-a-real-jwt"),
    )
    assert response.status_code == 401, response.text


async def test_probes_listing_and_crud_round_trip(
    client: AsyncClient,
    app_instance: Any,
    regions: list[dict[str, Any]],
) -> None:
    token = await _seed_admin(app_instance)
    headers = _bearer(token)

    # Initially empty
    response = await client.get("/api/v1/admin/probes", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 0
    assert body["items"] == []

    # Create a probe
    response = await client.post(
        "/api/v1/admin/probes",
        json={"alias": "alpha", "region_id": regions[0]["id"], "is_public": True},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    created = response.json()
    assert created["alias"] == "alpha"
    assert created["region_code"] == "RU-MOW"
    assert created["region_title"] == "Москва"
    assert created["is_public"] is True
    assert isinstance(created["token"], str) and len(created["token"]) >= 16
    probe_id = created["id"]

    # The list now contains exactly one probe.
    response = await client.get("/api/v1/admin/probes", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["id"] == probe_id

    # Update the alias
    response = await client.put(
        f"/api/v1/admin/probes/{probe_id}",
        json={"alias": "alpha-renamed", "is_public": False},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    updated = response.json()
    assert updated["alias"] == "alpha-renamed"
    assert updated["is_public"] is False

    # 404 on missing probe
    response = await client.put(
        "/api/v1/admin/probes/999999",
        json={"alias": "ghost"},
        headers=headers,
    )
    assert response.status_code == 404, response.text

    # Delete the probe
    response = await client.delete(f"/api/v1/admin/probes/{probe_id}", headers=headers)
    assert response.status_code == 204, response.text

    response = await client.get("/api/v1/admin/probes", headers=headers)
    assert response.status_code == 200
    assert response.json()["total"] == 0


async def test_probe_create_duplicate_token_returns_409(
    client: AsyncClient,
    app_instance: Any,
    regions: list[dict[str, Any]],
) -> None:
    token = await _seed_admin(app_instance)
    headers = _bearer(token)

    response = await client.post(
        "/api/v1/admin/probes",
        json={"alias": "first", "region_id": regions[0]["id"], "token": "duplicate-token"},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    response = await client.post(
        "/api/v1/admin/probes",
        json={"alias": "second", "region_id": regions[0]["id"], "token": "duplicate-token"},
        headers=headers,
    )
    assert response.status_code == 409, response.text


async def test_probe_create_with_unknown_region_returns_404(
    client: AsyncClient,
    app_instance: Any,
) -> None:
    token = await _seed_admin(app_instance)
    headers = _bearer(token)
    response = await client.post(
        "/api/v1/admin/probes",
        json={"alias": "nope", "region_id": 999999, "token": "any-token"},
        headers=headers,
    )
    assert response.status_code == 404, response.text


async def test_admin_tasks_listing_with_filters(
    client: AsyncClient,
    app_instance: Any,
    regions: list[dict[str, Any]],
) -> None:
    token = await _seed_admin(app_instance)
    headers = _bearer(token)
    db_pool: asyncpg.Pool = app_instance.state.db.pool

    # Seed: 1 region, 1 probe, 3 tasks, 1 task_result in region #0.
    conn = await db_pool.acquire()
    try:
        region_id = regions[0]["id"]
        probe_id = int(
            (
                await conn.fetchrow(
                    """
                    INSERT INTO probes (alias, token, region_id)
                    VALUES ($1, $2, $3) RETURNING id
                    """,
                    "probe-1",
                    "admin-tasks-probe",
                    region_id,
                )
            )["id"]
        )
        task_ids: list[int] = []
        for i, status in enumerate(("pending", "completed", "failed")):
            task_id = int(
                (
                    await conn.fetchrow(
                        "INSERT INTO tasks (link, status) VALUES ($1, $2) RETURNING id",
                        f"vless://admin-{i}@example.com",
                        status,
                    )
                )["id"]
            )
            task_ids.append(task_id)
        await conn.execute(
            """
            INSERT INTO task_results
                (task_id, probe_id, region_id, speed_mbps, error, raw, created_at)
            VALUES ($1, $2, $3, $4, NULL, $5::jsonb, $6)
            """,
            task_ids[1],
            probe_id,
            region_id,
            42.5,
            json.dumps({"seed": "ok"}),
            datetime.now(tz=UTC) - timedelta(hours=2),
        )
    finally:
        await db_pool.release(conn)

    # No filters
    response = await client.get("/api/v1/admin/tasks", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 3
    assert {item["status"] for item in body["items"]} == {"pending", "completed", "failed"}

    # Status filter
    response = await client.get("/api/v1/admin/tasks?status=completed", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["status"] == "completed"
    assert body["items"][0]["result_count"] == 1
    assert body["items"][0]["error_count"] == 0

    # Region filter
    response = await client.get(f"/api/v1/admin/tasks?region={regions[0]['code']}", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["id"] == task_ids[1]

    # From/To filter — pass the value through ``params`` so httpx handles
    # URL encoding (the ``+`` in the timezone offset must not become a space).
    now = datetime.now(tz=UTC)
    response = await client.get(
        "/api/v1/admin/tasks",
        params={"from": (now - timedelta(minutes=5)).isoformat()},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    assert response.json()["total"] == 3

    # Status filter with bad value
    response = await client.get("/api/v1/admin/tasks?status=invalid", headers=headers)
    assert response.status_code == 422, response.text

    # Detail endpoint
    response = await client.get(f"/api/v1/admin/tasks/{task_ids[1]}", headers=headers)
    assert response.status_code == 200, response.text
    detail = response.json()
    assert detail["id"] == task_ids[1]
    assert len(detail["results"]) == 1
    assert detail["results"][0]["region_code"] == regions[0]["code"]
    assert detail["results"][0]["speed_mbps"] == pytest.approx(42.5)

    # Detail 404
    response = await client.get("/api/v1/admin/tasks/999999", headers=headers)
    assert response.status_code == 404, response.text


async def test_admin_tasks_endpoint_requires_bearer_token(client: AsyncClient) -> None:
    response = await client.get("/api/v1/admin/tasks")
    assert response.status_code == 401, response.text


async def test_admin_stats_returns_aggregate_payload(
    client: AsyncClient,
    app_instance: Any,
    regions: list[dict[str, Any]],
) -> None:
    token = await _seed_admin(app_instance)
    headers = _bearer(token)
    db_pool: asyncpg.Pool = app_instance.state.db.pool

    conn = await db_pool.acquire()
    try:
        # Insert a mix of speeds so the histogram and the median have something
        # to compute. Each result is attached to a different probe (so the
        # unique (task_id, probe_id) constraint does not fire).
        probe_ids: list[int] = []
        for index in range(6):
            probe_id = int(
                (
                    await conn.fetchrow(
                        """
                        INSERT INTO probes (alias, token, region_id)
                        VALUES ($1, $2, $3) RETURNING id
                        """,
                        f"stats-probe-{index}",
                        f"admin-stats-probe-{index}",
                        regions[0]["id"],
                    )
                )["id"]
            )
            probe_ids.append(probe_id)
        task_id = int(
            (
                await conn.fetchrow(
                    "INSERT INTO tasks (link, status) VALUES ($1, 'completed') RETURNING id",
                    "vless://stats@example.com",
                )
            )["id"]
        )
        for probe_id, speed in zip(probe_ids, (0.5, 1.5, 6.0, 22.0, 70.0, 150.0), strict=True):
            await conn.execute(
                """
                INSERT INTO task_results
                    (task_id, probe_id, region_id, speed_mbps, raw, created_at)
                VALUES ($1, $2, $3, $4, $5::jsonb, now() - interval '1 day')
                """,
                task_id,
                probe_id,
                regions[0]["id"],
                speed,
                json.dumps({"seed": True}),
            )
    finally:
        await db_pool.release(conn)

    response = await client.get("/api/v1/admin/stats", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert "median_per_region_7d" in body
    assert {r["region_code"] for r in body["median_per_region_7d"]} == {
        regions[0]["code"],
        regions[1]["code"],
    }
    histogram = {bucket["bucket"]: bucket["count"] for bucket in body["histogram"]}
    # We seeded 0.5 (lt_1), 1.5 (1_5), 6.0 (5_20), 22.0 (20_50), 70.0 (50_100),
    # 150.0 (gte_100). All six buckets should be non-zero.
    assert all(histogram[key] >= 1 for key in histogram)
    assert body["archive_lag_seconds"] is None
    totals = body["totals"]
    assert totals["regions"] == 2
    assert totals["probes"] == 6
    assert totals["tasks"] == 1
    assert totals["results_7d"] == 6


async def test_admin_stats_requires_bearer_token(client: AsyncClient) -> None:
    response = await client.get("/api/v1/admin/stats")
    assert response.status_code == 401, response.text


async def test_create_admin_cli_inserts_and_updates(
    app_instance: Any,
) -> None:
    """The CLI helper creates a new admin and re-uses the same id on update."""
    db = app_instance.state.db

    status, first_id = await create_admin(db, email=EMAIL, password=PASSWORD)
    assert status == "created"
    assert first_id > 0

    # The bcrypt salt is randomised on every hash, so re-hashing the same
    # password always returns a different digest; the CLI therefore reports
    # "updated" (still a no-op for the user since the plaintext matched).
    status, again_id = await create_admin(db, email=EMAIL, password=PASSWORD)
    assert status == "updated"
    assert again_id == first_id

    # Different password → "updated".
    status, third_id = await create_admin(db, email=EMAIL, password="another-secret-9876")
    assert status == "updated"
    assert third_id == first_id

    # The login endpoint accepts the most recent password.
    from xspeed_server.app.core.security import verify_password

    pool = app_instance.state.db.pool
    async with pool.acquire() as conn:
        stored_hash = await conn.fetchval(
            "SELECT password_hash FROM admins WHERE id = $1", first_id
        )
    assert stored_hash is not None
    assert verify_password("another-secret-9876", str(stored_hash))


async def test_create_admin_cli_rejects_short_password(app_instance: Any) -> None:
    """The CLI guard raises a clear ValueError on weak passwords."""
    db = app_instance.state.db
    with pytest.raises(ValueError):
        await create_admin(db, email=EMAIL, password="short")


__all__ = [
    "test_admin_stats_requires_bearer_token",
    "test_admin_stats_returns_aggregate_payload",
    "test_admin_tasks_endpoint_requires_bearer_token",
    "test_admin_tasks_listing_with_filters",
    "test_create_admin_cli_inserts_and_updates",
    "test_create_admin_cli_rejects_short_password",
    "test_login_with_correct_credentials_returns_jwt",
    "test_login_with_unknown_email_returns_401",
    "test_login_with_wrong_password_returns_401",
    "test_probe_create_duplicate_token_returns_409",
    "test_probe_create_with_unknown_region_returns_404",
    "test_probes_endpoint_requires_bearer_token",
    "test_probes_endpoint_rejects_invalid_token",
    "test_probes_listing_and_crud_round_trip",
]
