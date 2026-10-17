"""End-to-end tests for the admin metrics endpoints (DAT-446).

Exercises the JWT-protected journal surface used by the admin panel:

* ``GET  /api/v1/admin/metrics`` — paginated list joined with
  ``probes`` and ``regions``; requires a bearer token.
* ``POST /api/v1/admin/metrics`` — append a sample; rejects missing
  ``server_id`` with ``422`` and unknown ``server_id`` with ``404
  Server not found``.
"""

from __future__ import annotations

import os
from typing import Any

import asyncpg
from httpx import AsyncClient


def _test_dsn() -> str:
    url = os.environ["TEST_DATABASE_URL"]
    if url.startswith("postgresql+asyncpg://"):
        return "postgresql://" + url[len("postgresql+asyncpg://") :]
    return url


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _seed_admin(app_instance: Any) -> str:
    """Insert an admin record through the public CLI helper and return the JWT."""
    from xspeed_server.app.admin_cli import create_admin
    from xspeed_server.app.config import get_settings
    from xspeed_server.app.core.security import JWTService

    email = "admin-metrics@xspeed.ru"
    password = "supersecret-pw-1234"
    db = app_instance.state.db
    await create_admin(db, email=email, password=password)
    jwt_service = JWTService.from_settings(get_settings())
    return jwt_service.create_access_token(subject=email)


async def _seed_region_probe(db_pool: asyncpg.Pool) -> int:
    """Insert a minimal region + probe pair and return the probe id."""
    async with db_pool.acquire() as conn:
        region_id = await conn.fetchval(
            "INSERT INTO regions (code, title) VALUES ($1, $2) RETURNING id",
            "RU-TST",
            "Тестовый регион",
        )
        probe_id = await conn.fetchval(
            """
            INSERT INTO probes (alias, token, region_id)
            VALUES ($1, $2, $3)
            RETURNING id
            """,
            "test-probe",
            "test-token-1234567890",
            int(region_id),
        )
    return int(probe_id)


async def test_admin_metrics_endpoint_requires_bearer_token(client: AsyncClient) -> None:
    response = await client.get("/api/v1/admin/metrics")
    assert response.status_code == 401, response.text


async def test_admin_metric_create_rejects_missing_server_id(
    client: AsyncClient, app_instance: Any
) -> None:
    token = await _seed_admin(app_instance)
    headers = _bearer(token)

    response = await client.post(
        "/api/v1/admin/metrics",
        json={
            "client_ip": "203.0.113.5",
            "metric_alias": "download_mbps",
            "value": 42.5,
        },
        headers=headers,
    )
    assert response.status_code == 422, response.text
    detail = response.json()
    assert any(item["loc"] == ["body", "server_id"] for item in detail["detail"])


async def test_admin_metric_create_rejects_unknown_server(
    client: AsyncClient, app_instance: Any
) -> None:
    token = await _seed_admin(app_instance)
    headers = _bearer(token)

    response = await client.post(
        "/api/v1/admin/metrics",
        json={
            "client_ip": "203.0.113.5",
            "server_id": 999999,
            "metric_alias": "download_mbps",
            "value": 42.5,
        },
        headers=headers,
    )
    assert response.status_code == 404, response.text
    assert response.json()["detail"] == "Server not found"


async def test_admin_metric_create_round_trip(client: AsyncClient, app_instance: Any) -> None:
    token = await _seed_admin(app_instance)
    headers = _bearer(token)
    db_pool: asyncpg.Pool = app_instance.state.db.pool
    probe_id = await _seed_region_probe(db_pool)

    response = await client.get("/api/v1/admin/metrics", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 0
    assert body["items"] == []

    response = await client.post(
        "/api/v1/admin/metrics",
        json={
            "client_ip": "203.0.113.5",
            "client_isp": "Acme ISP",
            "client_lat": 43.119,
            "client_lng": 131.886,
            "server_id": probe_id,
            "ip": "10.0.0.5",
            "metric_alias": "download_mbps",
            "value": 42.5,
        },
        headers=headers,
    )
    assert response.status_code == 201, response.text
    created = response.json()
    assert created["client_ip"] == "203.0.113.5"
    assert created["client_isp"] == "Acme ISP"
    assert created["server_id"] == probe_id
    assert created["server_alias"] == "test-probe"
    assert created["region_code"] == "RU-TST"
    assert created["metric_alias"] == "download_mbps"
    assert created["value"] == 42.5
    assert created["created_at"]
    metric_id = created["id"]

    response = await client.get("/api/v1/admin/metrics", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["id"] == metric_id

    # GET by id
    response = await client.get(f"/api/v1/admin/metrics/{metric_id}", headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["id"] == metric_id


async def test_admin_metric_create_with_null_optional_fields(
    client: AsyncClient, app_instance: Any
) -> None:
    token = await _seed_admin(app_instance)
    headers = _bearer(token)
    db_pool: asyncpg.Pool = app_instance.state.db.pool
    probe_id = await _seed_region_probe(db_pool)

    response = await client.post(
        "/api/v1/admin/metrics",
        json={
            "client_ip": "198.51.100.7",
            "server_id": probe_id,
            "metric_alias": "online",
        },
        headers=headers,
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["client_isp"] is None
    assert body["client_lat"] is None
    assert body["client_lng"] is None
    assert body["ip"] is None
    assert body["value"] is None


async def test_admin_metric_pagination(client: AsyncClient, app_instance: Any) -> None:
    token = await _seed_admin(app_instance)
    headers = _bearer(token)
    db_pool: asyncpg.Pool = app_instance.state.db.pool
    probe_id = await _seed_region_probe(db_pool)

    async with db_pool.acquire() as conn:
        for idx in range(3):
            await conn.execute(
                """
                INSERT INTO metrics (
                    client_ip, server_id, metric_alias, value, created_at
                )
                VALUES ($1, $2, $3, $4, now() + ($5 || ' seconds')::interval)
                """,
                f"203.0.113.{idx + 1}",
                probe_id,
                "download_mbps",
                10.0 + idx,
                str(idx),
            )

    response = await client.get("/api/v1/admin/metrics?limit=1", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 3
    assert body["limit"] == 1
    assert len(body["items"]) == 1
    # newest first (created_at DESC)
    assert body["items"][0]["client_ip"] == "203.0.113.3"

    response = await client.get("/api/v1/admin/metrics?limit=1&offset=1", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["items"][0]["client_ip"] == "203.0.113.2"


__all__ = [
    "test_admin_metric_create_rejects_missing_server_id",
    "test_admin_metric_create_rejects_unknown_server",
    "test_admin_metric_create_round_trip",
    "test_admin_metric_create_with_null_optional_fields",
    "test_admin_metric_pagination",
    "test_admin_metrics_endpoint_requires_bearer_token",
]
