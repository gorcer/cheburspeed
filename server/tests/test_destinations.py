"""End-to-end tests for the admin destinations endpoints (DAT-431).

Exercises the JWT-protected CRUD surface used by the admin panel:

* ``GET  /api/v1/admin/destinations`` — paginated list.
* ``POST /api/v1/admin/destinations`` — create a destination; rejects
  duplicate names and invalid URLs.
* ``PUT  /api/v1/admin/destinations/{id}`` — patch name/url.
* ``DELETE /api/v1/admin/destinations/{id}`` — drop a destination;
  refuses to delete when a task still references it.
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

    email = "admin-destinations@xspeed.ru"
    password = "supersecret-pw-1234"
    db = app_instance.state.db
    await create_admin(db, email=email, password=password)
    jwt_service = JWTService.from_settings(get_settings())
    return jwt_service.create_access_token(subject=email)


async def test_admin_destinations_endpoint_requires_bearer_token(client: AsyncClient) -> None:
    response = await client.get("/api/v1/admin/destinations")
    assert response.status_code == 401, response.text


async def test_admin_destination_create_rejects_invalid_url(
    client: AsyncClient, app_instance: Any
) -> None:
    token = await _seed_admin(app_instance)
    headers = _bearer(token)

    response = await client.post(
        "/api/v1/admin/destinations",
        json={"name": "Bad", "url": "javascript:alert(1)"},
        headers=headers,
    )
    assert response.status_code == 422, response.text
    detail = response.json()
    assert any(item["loc"] == ["body", "url"] for item in detail["detail"])


async def test_admin_destination_create_round_trip(client: AsyncClient, app_instance: Any) -> None:
    token = await _seed_admin(app_instance)
    headers = _bearer(token)

    # Initially empty
    response = await client.get("/api/v1/admin/destinations", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 0
    assert body["items"] == []

    # Create
    response = await client.post(
        "/api/v1/admin/destinations",
        json={
            "name": "Cloudflare",
            "url": "https://speed.example.com/__down?bytes=100000000",
        },
        headers=headers,
    )
    assert response.status_code == 201, response.text
    created = response.json()
    assert created["name"] == "Cloudflare"
    assert created["url"] == "https://speed.example.com/__down?bytes=100000000"
    destination_id = created["id"]

    # List now contains exactly one row.
    response = await client.get("/api/v1/admin/destinations", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["id"] == destination_id

    # Update
    response = await client.put(
        f"/api/v1/admin/destinations/{destination_id}",
        json={"name": "Cloudflare-renamed"},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    updated = response.json()
    assert updated["name"] == "Cloudflare-renamed"
    assert updated["url"] == "https://speed.example.com/__down?bytes=100000000"

    # 404 on unknown
    response = await client.put(
        "/api/v1/admin/destinations/999999",
        json={"name": "ghost"},
        headers=headers,
    )
    assert response.status_code == 404, response.text

    # Delete
    response = await client.delete(
        f"/api/v1/admin/destinations/{destination_id}",
        headers=headers,
    )
    assert response.status_code == 204, response.text

    response = await client.get("/api/v1/admin/destinations", headers=headers)
    assert response.status_code == 200
    assert response.json()["total"] == 0


async def test_admin_destination_duplicate_name_returns_409(
    client: AsyncClient, app_instance: Any
) -> None:
    token = await _seed_admin(app_instance)
    headers = _bearer(token)

    payload = {"name": "Cloudflare", "url": "https://example.com/__down"}
    response = await client.post(
        "/api/v1/admin/destinations",
        json=payload,
        headers=headers,
    )
    assert response.status_code == 201, response.text

    response = await client.post(
        "/api/v1/admin/destinations",
        json=payload,
        headers=headers,
    )
    assert response.status_code == 409, response.text


async def test_admin_destination_delete_blocked_by_tasks(
    client: AsyncClient, app_instance: Any
) -> None:
    token = await _seed_admin(app_instance)
    headers = _bearer(token)
    db_pool: asyncpg.Pool = app_instance.state.db.pool

    response = await client.post(
        "/api/v1/admin/destinations",
        json={"name": "Cloudflare", "url": "https://example.com/__down"},
        headers=headers,
    )
    assert response.status_code == 201
    destination_id = response.json()["id"]

    # Seed a task that references the destination.
    async with db_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO tasks (link, destination_id) VALUES ($1, $2)",
            "vless://11111111-2222-3333-4444-555555555555@example.com:443?security=reality&sni=cdn.example.com&type=tcp#demo",
            destination_id,
        )

    response = await client.delete(
        f"/api/v1/admin/destinations/{destination_id}",
        headers=headers,
    )
    assert response.status_code == 409, response.text


__all__ = [
    "test_admin_destination_create_rejects_invalid_url",
    "test_admin_destination_create_round_trip",
    "test_admin_destination_delete_blocked_by_tasks",
    "test_admin_destination_duplicate_name_returns_409",
    "test_admin_destinations_endpoint_requires_bearer_token",
]
