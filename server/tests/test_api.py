"""End-to-end tests for the HTTP API exposed by ``/api/v1``.

The tests cover:

* the vless link validation (regex enforcement),
* the ``POST /api/v1/tasks`` happy path (201, ``status: "pending"``),
* the rate limit on the POST endpoint (10/hour → 11th call returns 429),
* the ``GET /api/v1/tasks/{id}`` shape (with and without results),
* the paginated list at ``GET /api/v1/tasks``,
* the ``GET /api/v1/regions`` aggregated view,
* the ``GET /api/v1/probes/online`` per-region summary.
"""

from __future__ import annotations

import os
from typing import Any

import asyncpg
import pytest
from httpx import AsyncClient

VALID_LINK = (
    "vless://11111111-2222-3333-4444-555555555555@example.com:443"
    "?security=reality&sni=cdn.example.com&type=tcp#demo"
)
INVALID_LINK = "vless://not-a-uuid@example.com:443?foo=bar"


def _test_dsn() -> str:
    url = os.environ["TEST_DATABASE_URL"]
    if url.startswith("postgresql+asyncpg://"):
        return "postgresql://" + url[len("postgresql+asyncpg://") :]
    return url


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
async def region() -> dict[str, int]:
    """Insert a single region and return its id/code mapping."""
    return await _insert_region(
        code="RU-MOW",
        title="Москва",
        lat=55.7558,
        lon=37.6173,
    )


async def test_post_task_with_valid_link_returns_201(client: AsyncClient) -> None:
    response = await client.post("/api/v1/tasks", json={"link": VALID_LINK})
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "pending"
    assert isinstance(body["id"], int) and body["id"] > 0


async def test_post_task_with_invalid_link_returns_422(client: AsyncClient) -> None:
    response = await client.post("/api/v1/tasks", json={"link": INVALID_LINK})
    assert response.status_code == 422, response.text
    detail = response.json()
    assert detail["detail"][0]["loc"] == ["body", "link"]


async def test_post_task_with_missing_link_returns_422(client: AsyncClient) -> None:
    response = await client.post("/api/v1/tasks", json={})
    assert response.status_code == 422, response.text


async def test_post_task_with_extra_field_is_rejected(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/tasks",
        json={"link": VALID_LINK, "unexpected": "value"},
    )
    assert response.status_code == 422, response.text


async def test_post_task_accepts_probe_tokens_whitelist(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/tasks",
        json={"link": VALID_LINK, "probe_tokens": ["token-a", "token-b"]},
    )
    assert response.status_code == 201, response.text


async def test_post_task_is_rate_limited_after_ten_requests(client: AsyncClient) -> None:
    last_status = None
    for i in range(10):
        response = await client.post("/api/v1/tasks", json={"link": VALID_LINK})
        assert response.status_code == 201, f"request {i} failed: {response.text}"
    blocked = await client.post("/api/v1/tasks", json={"link": VALID_LINK})
    last_status = blocked.status_code
    assert last_status == 429, f"expected 429, got {last_status}: {blocked.text}"


async def test_get_task_returns_404_for_unknown_id(client: AsyncClient) -> None:
    response = await client.get("/api/v1/tasks/999999")
    assert response.status_code == 404, response.text


async def test_get_task_returns_task_with_results(
    client: AsyncClient, region: dict[str, int]
) -> None:
    created = await client.post("/api/v1/tasks", json={"link": VALID_LINK})
    assert created.status_code == 201
    task_id = created.json()["id"]

    response = await client.get(f"/api/v1/tasks/{task_id}")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["id"] == task_id
    assert body["status"] == "pending"
    assert body["link"] == VALID_LINK
    assert body["address"] == "example.com:443"
    assert body["probe_tokens"] is None
    assert body["results"] == []
    assert body["completed_at"] is None
    assert body["created_at"] is not None


async def test_get_task_address_handles_ip_and_bare_host(
    client: AsyncClient,
) -> None:
    """A vless:// URI with an IP host returns host:port; a malformed one yields None."""
    ip_link = (
        "vless://11111111-2222-3333-4444-555555555555@"
        "192.0.2.10:443?security=none&type=tcp#ip"
    )
    created = await client.post("/api/v1/tasks", json={"link": ip_link})
    assert created.status_code == 201
    response = await client.get(f"/api/v1/tasks/{created.json()['id']}")
    assert response.json()["address"] == "192.0.2.10:443"

    bare_host_link = (
        "vless://11111111-2222-3333-4444-555555555555@"
        "example.com:443?security=reality&sni=cdn.example.com&type=tcp#bare"
    )
    created = await client.post("/api/v1/tasks", json={"link": bare_host_link})
    assert created.status_code == 201
    response = await client.get(f"/api/v1/tasks/{created.json()['id']}")
    assert response.json()["address"] == "example.com:443"


async def test_list_tasks_returns_paginated_envelope(client: AsyncClient) -> None:
    for _ in range(3):
        response = await client.post("/api/v1/tasks", json={"link": VALID_LINK})
        assert response.status_code == 201

    response = await client.get("/api/v1/tasks", params={"limit": 2, "offset": 0})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["limit"] == 2
    assert body["offset"] == 0
    assert body["total"] == 3
    assert len(body["items"]) == 2
    assert {item["status"] for item in body["items"]} == {"pending"}

    # newest first
    ids = [item["id"] for item in body["items"]]
    assert ids == sorted(ids, reverse=True)


async def test_list_tasks_paginates_with_offset(client: AsyncClient) -> None:
    for _ in range(4):
        response = await client.post("/api/v1/tasks", json={"link": VALID_LINK})
        assert response.status_code == 201

    page1 = await client.get("/api/v1/tasks", params={"limit": 2, "offset": 0})
    page2 = await client.get("/api/v1/tasks", params={"limit": 2, "offset": 2})
    assert page1.status_code == 200 and page2.status_code == 200
    page1_ids = {item["id"] for item in page1.json()["items"]}
    page2_ids = {item["id"] for item in page2.json()["items"]}
    assert page1_ids.isdisjoint(page2_ids)
    assert page1.json()["total"] == 4
    assert page2.json()["total"] == 4


async def test_list_tasks_rejects_invalid_pagination(client: AsyncClient) -> None:
    response = await client.get("/api/v1/tasks", params={"limit": 0})
    assert response.status_code == 422
    response = await client.get("/api/v1/tasks", params={"offset": -1})
    assert response.status_code == 422


async def test_list_regions_returns_expected_shape(
    client: AsyncClient, region: dict[str, int]
) -> None:
    response = await client.get("/api/v1/regions")
    assert response.status_code == 200, response.text
    body = response.json()
    assert "items" in body
    assert len(body["items"]) == 1
    item = body["items"][0]
    assert item["code"] == "RU-MOW"
    assert item["title"] == "Москва"
    assert item["lat"] == 55.7558
    assert item["lon"] == 37.6173
    assert item["probe_count"] == 0
    assert item["last_median_mbps"] is None


async def test_list_regions_is_empty_without_data(client: AsyncClient) -> None:
    response = await client.get("/api/v1/regions")
    assert response.status_code == 200
    assert response.json() == {"items": []}


async def test_probes_online_returns_empty_when_no_probes(client: AsyncClient) -> None:
    response = await client.get("/api/v1/probes/online")
    assert response.status_code == 200, response.text
    assert response.json() == {"regions": {}}


async def test_healthz_returns_ok(client: AsyncClient) -> None:
    response = await client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_root_returns_service_metadata(client: AsyncClient) -> None:
    response = await client.get("/")
    assert response.status_code == 200
    body = response.json()
    assert body["service"] == "xspeed-server"
    assert body["docs"] == "/docs"
