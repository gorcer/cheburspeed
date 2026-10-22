"""Tests for the WebSocket task dispatcher.

These cover the four contracts the dispatcher must honour:

* :func:`claim_task` picks the oldest pending task whose whitelist
  admits the supplied probe — and never the same task twice when two
  probes race for the queue (``FOR UPDATE SKIP LOCKED``),
* :func:`check_completion` flips a single-probe task to
  ``completed`` the moment a result lands,
* :func:`check_completion` resets multi-probe tasks to ``pending``
  until every distinct probe in the whitelist has reported,
* :func:`sweep_stale_running_tasks` revives ``running`` tasks that
  never produced a result inside the staleness window.
"""

from __future__ import annotations

import json
from typing import Any

import asyncpg
import pytest

from xspeed_server.app.db import Database
from xspeed_server.app.ws.dispatcher import (
    STALE_RUNNING_AFTER_SECONDS,
    ClaimedTask,
    check_completion,
    claim_task,
    dispatch_to_probe,
    record_result,
    sweep_stale_running_tasks,
)
from xspeed_server.app.ws.hub import Hub
from xspeed_server.app.ws.protocol import PROTOCOL_VERSION

from .conftest import _test_database_url

VALID_LINK = (
    "vless://11111111-2222-3333-4444-555555555555@example.com:443"
    "?security=reality&sni=cdn.example.com&type=tcp#demo"
)

VALID_AWG_LINK = """[Interface]
PrivateKey = cNx0N+nxLtxHhbHG1M1tM8pDvDNnOWGbkpHFrTnHqAA=
Address = 10.8.40.106/32
DNS = 1.1.1.1,1.0.0.1
Jc = 3
Jmin = 10
Jmax = 50
S1 = 150
S2 = 224
S3 = 0
S4 = 0
H1 = 1232863036
H2 = 1441878557
H3 = 636923766
H4 = 214107436

[Peer]
PublicKey = fgI6VqMCIvjErRo2vL6XQhDqfBiK+FTxLBRLxMCCZVU=
PresharedKey = pkcDjyh0jW8RFTLvhEEeMk4SPT7WUEPGyN/ZXGQE2Vk=
Endpoint = in.oneyearsolution.com:40974
AllowedIPs = 0.0.0.0/0, ::/0
PersistentKeepalive = 25
"""


def _test_dsn() -> str:
    url = _test_database_url()
    if url.startswith("postgresql+asyncpg://"):
        return "postgresql://" + url[len("postgresql+asyncpg://") :]
    return url


async def _seed_region(code: str = "RU-MOW", title: str = "Москва") -> int:
    conn = await asyncpg.connect(_test_dsn())
    try:
        row = await conn.fetchrow(
            """
            INSERT INTO regions (code, title, lat, lon)
            VALUES ($1, $2, 55.7558, 37.6173)
            RETURNING id
            """,
            code,
            title,
        )
        return int(row["id"])
    finally:
        await conn.close()


async def _seed_probe(token: str, region_id: int, *, is_public: bool = True) -> int:
    conn = await asyncpg.connect(_test_dsn())
    try:
        row = await conn.fetchrow(
            """
            INSERT INTO probes (alias, token, region_id, is_public)
            VALUES ($1, $2, $3, $4)
            RETURNING id
            """,
            f"probe-{token[:4]}",
            token,
            region_id,
            is_public,
        )
        return int(row["id"])
    finally:
        await conn.close()


async def _insert_task(
    link: str,
    probe_tokens: list[str] | None = None,
    status: str = "pending",
    protocol: str = "vless",
) -> int:
    conn = await asyncpg.connect(_test_dsn())
    try:
        row = await conn.fetchrow(
            """
            INSERT INTO tasks (link, protocol, status, probe_tokens)
            VALUES ($1, $2, $3, $4::jsonb)
            RETURNING id
            """,
            link,
            protocol,
            status,
            None if probe_tokens is None else json.dumps(probe_tokens),
        )
        return int(row["id"])
    finally:
        await conn.close()


@pytest.fixture
async def app_db(app_instance: Any) -> Database:
    """Return the :class:`Database` instance attached to the test app."""
    return app_instance.state.db  # type: ignore[no-any-return]


async def test_claim_task_returns_none_when_queue_is_empty(app_db: Database) -> None:
    """With no pending tasks the dispatcher yields ``None``."""
    async with app_db.pool.acquire() as conn:
        claimed = await claim_task(conn, probe_token="any-token")
    assert claimed is None


async def test_claim_task_picks_oldest_pending_task(app_db: Database) -> None:
    """The oldest pending task is reserved first."""
    region_id = await _seed_region()
    await _seed_probe("probe-x", region_id)
    first = await _insert_task(VALID_LINK)
    second = await _insert_task(VALID_LINK)

    async with app_db.pool.acquire() as conn:
        claimed = await claim_task(conn, probe_token="probe-x")

    assert claimed is not None
    assert claimed.id == first
    assert claimed.link == VALID_LINK
    assert claimed.id != second


async def test_claim_task_filters_by_probe_tokens_whitelist(app_db: Database) -> None:
    """A probe outside the whitelist cannot pick up the task."""
    region_id = await _seed_region()
    await _seed_probe("probe-a", region_id)
    allowed_task = await _insert_task(VALID_LINK, probe_tokens=["probe-a"])
    other_task = await _insert_task(VALID_LINK, probe_tokens=["probe-b"])

    async with app_db.pool.acquire() as conn:
        claimed = await claim_task(conn, probe_token="probe-a")

    assert claimed is not None
    assert claimed.id == allowed_task
    assert claimed.id != other_task


async def test_claim_task_skips_private_probe_for_public_task(app_db: Database) -> None:
    """Only probes marked public may consume tasks without a whitelist."""
    region_id = await _seed_region()
    await _seed_probe("private-probe", region_id, is_public=False)
    await _insert_task(VALID_LINK)

    async with app_db.pool.acquire() as conn:
        claimed = await claim_task(conn, probe_token="private-probe")

    assert claimed is None


async def test_claim_task_marks_running_with_assigned_to(app_db: Database) -> None:
    """Successful claim updates ``tasks.status`` and ``assigned_to``."""
    region_id = await _seed_region()
    await _seed_probe("probe-claim", region_id)
    task_id = await _insert_task(VALID_LINK)

    async with app_db.pool.acquire() as conn:
        await claim_task(conn, probe_token="probe-claim")
        row = await conn.fetchrow(
            "SELECT status, assigned_to, started_at FROM tasks WHERE id = $1",
            task_id,
        )

    assert row["status"] == "running"
    assert row["assigned_to"] == "probe-claim"
    assert row["started_at"] is not None


async def test_dispatch_distributes_tasks_to_probes_without_duplicates(
    app_db: Database,
) -> None:
    """Two probes race for three tasks — every probe gets a distinct task."""
    region_id = await _seed_region()
    await _seed_probe("probe-a", region_id)
    await _seed_probe("probe-b", region_id)
    await _insert_task(VALID_LINK)
    await _insert_task(VALID_LINK)
    await _insert_task(VALID_LINK)

    hub = Hub()
    dispatched: list[int] = []
    sent: list[str] = []

    async def fake_send(token: str, message: dict[str, Any]) -> bool:
        sent.append(token)
        if message.get("type") == "task.assigned":
            dispatched.append(int(message["task_id"]))
            return True
        return False

    hub.send = fake_send  # type: ignore[method-assign]

    async with app_db.pool.acquire():
        await dispatch_to_probe(app_db, hub, "probe-a")
    async with app_db.pool.acquire():
        await dispatch_to_probe(app_db, hub, "probe-b")

    assert len(dispatched) == 2
    assert len(set(dispatched)) == 2  # no duplicates
    assert set(sent) == {"probe-a", "probe-b"}


async def test_check_completion_marks_single_probe_task_completed(
    app_db: Database,
) -> None:
    """A task with no whitelist flips to ``completed`` after one result."""
    region_id = await _seed_region()
    probe_token = "probe-single"
    probe_id = await _seed_probe(probe_token, region_id)
    task_id = await _insert_task(VALID_LINK)

    async with app_db.pool.acquire() as conn:
        await claim_task(conn, probe_token=probe_token)
        await record_result(
            conn,
            task_id=task_id,
            probe_id=probe_id,
            region_id=region_id,
            speed_mbps=10.5,
            error=None,
            raw={"speed_mbps": 10.5, "error": None},
        )
        completed = await check_completion(conn, task_id)
        row = await conn.fetchrow(
            "SELECT status, completed_at, assigned_to FROM tasks WHERE id = $1",
            task_id,
        )

    assert completed is True
    assert row["status"] == "completed"
    assert row["completed_at"] is not None
    assert row["assigned_to"] is None


async def test_check_completion_resets_multi_probe_task_to_pending(
    app_db: Database,
) -> None:
    """With a multi-probe whitelist the task stays pending until everyone reports."""
    region_id = await _seed_region()
    task_id = await _insert_task(VALID_LINK, probe_tokens=["probe-a", "probe-b"])
    probe_a = await _seed_probe("probe-a", region_id)
    probe_b = await _seed_probe("probe-b", region_id)

    async with app_db.pool.acquire() as conn:
        await claim_task(conn, probe_token="probe-a")
        await record_result(
            conn,
            task_id=task_id,
            probe_id=probe_a,
            region_id=region_id,
            speed_mbps=8.0,
            error=None,
            raw={"speed_mbps": 8.0, "error": None},
        )
        completed = await check_completion(conn, task_id)
        row = await conn.fetchrow("SELECT status, assigned_to FROM tasks WHERE id = $1", task_id)

    assert completed is False
    assert row["status"] == "pending"
    assert row["assigned_to"] is None

    # Second probe reports — task completes.
    async with app_db.pool.acquire() as conn:
        claimed_b = await claim_task(conn, probe_token="probe-b")
        assert claimed_b is not None
        await record_result(
            conn,
            task_id=task_id,
            probe_id=probe_b,
            region_id=region_id,
            speed_mbps=12.0,
            error=None,
            raw={"speed_mbps": 12.0, "error": None},
        )
        completed = await check_completion(conn, task_id)
        row = await conn.fetchrow("SELECT status, completed_at FROM tasks WHERE id = $1", task_id)

    assert completed is True
    assert row["status"] == "completed"
    assert row["completed_at"] is not None


async def test_completed_probe_cannot_claim_the_same_public_task_again(app_db: Database) -> None:
    """Public tasks are handed to each eligible probe at most once."""
    region_id = await _seed_region()
    first_probe = await _seed_probe("probe-a", region_id)
    await _seed_probe("probe-b", region_id)
    task_id = await _insert_task(VALID_LINK)

    async with app_db.pool.acquire() as conn:
        assert await claim_task(conn, probe_token="probe-a") is not None
        await record_result(
            conn,
            task_id=task_id,
            probe_id=first_probe,
            region_id=region_id,
            speed_mbps=8.0,
            error=None,
            raw={},
        )
        assert await check_completion(conn, task_id) is False
        assert await claim_task(conn, probe_token="probe-a") is None
        claimed = await claim_task(conn, probe_token="probe-b")

    assert claimed is not None
    assert claimed.id == task_id


async def test_sweep_resets_stale_running_tasks(app_db: Database) -> None:
    """``running`` tasks older than the threshold without results go back to pending."""
    task_id = await _insert_task(VALID_LINK)

    conn = await asyncpg.connect(_test_dsn())
    try:
        await conn.execute(
            """
            UPDATE tasks
            SET status = 'running',
                assigned_to = 'probe-stuck',
                started_at = now() - ($1 || ' seconds')::interval - interval '5 minutes'
            WHERE id = $2
            """,
            str(STALE_RUNNING_AFTER_SECONDS),
            task_id,
        )
    finally:
        await conn.close()

    swept = await sweep_stale_running_tasks(app_db)
    assert swept >= 1

    conn = await asyncpg.connect(_test_dsn())
    try:
        row = await conn.fetchrow(
            "SELECT status, assigned_to, started_at FROM tasks WHERE id = $1", task_id
        )
    finally:
        await conn.close()
    assert row["status"] == "pending"
    assert row["assigned_to"] is None
    assert row["started_at"] is None


async def test_sweep_leaves_recent_running_tasks_alone(app_db: Database) -> None:
    """A task that was just assigned is not yet stale."""
    region_id = await _seed_region()
    await _seed_probe("probe-fresh", region_id)
    task_id = await _insert_task(VALID_LINK)
    async with app_db.pool.acquire() as conn:
        await claim_task(conn, probe_token="probe-fresh")

    swept = await sweep_stale_running_tasks(app_db)
    # The fresh task must not be swept; ``swept`` may be zero or contain
    # rows from a previous test if they leaked, so we check directly.
    assert swept >= 0

    conn = await asyncpg.connect(_test_dsn())
    try:
        row = await conn.fetchrow("SELECT status, assigned_to FROM tasks WHERE id = $1", task_id)
    finally:
        await conn.close()
    assert row["status"] == "running"
    assert row["assigned_to"] == "probe-fresh"


async def test_record_result_overwrites_existing_row_for_same_probe(
    app_db: Database,
) -> None:
    """A probe retrying after a network blip updates the same row."""
    region_id = await _seed_region()
    probe_token = "probe-retry"
    probe_id = await _seed_probe(probe_token, region_id)
    task_id = await _insert_task(VALID_LINK)

    async with app_db.pool.acquire() as conn:
        await claim_task(conn, probe_token=probe_token)
        await record_result(
            conn,
            task_id=task_id,
            probe_id=probe_id,
            region_id=region_id,
            speed_mbps=5.0,
            error="timeout",
            raw={"speed_mbps": 5.0, "error": "timeout"},
        )
        await record_result(
            conn,
            task_id=task_id,
            probe_id=probe_id,
            region_id=region_id,
            speed_mbps=11.0,
            error=None,
            raw={"speed_mbps": 11.0, "error": None},
        )

    conn = await asyncpg.connect(_test_dsn())
    try:
        rows = await conn.fetch(
            "SELECT speed_mbps, error FROM task_results WHERE task_id = $1", task_id
        )
    finally:
        await conn.close()
    assert len(rows) == 1
    assert rows[0]["speed_mbps"] == pytest.approx(11.0)
    assert rows[0]["error"] is None


def test_protocol_version_constant_matches_spec() -> None:
    """The exported protocol version is what probes expect to see."""
    assert PROTOCOL_VERSION == 1


def test_claimed_task_is_a_dataclass_with_id_and_link() -> None:
    """Sanity check on the public :class:`ClaimedTask` shape."""
    sample = ClaimedTask(
        id=1,
        link=VALID_LINK,
        protocol="vless",
        destination_url=None,
        probe_public_ip="203.0.113.10",
    )
    assert sample.id == 1
    assert sample.link == VALID_LINK
    assert sample.protocol == "vless"
    assert sample.destination_url is None
    assert sample.probe_public_ip == "203.0.113.10"


async def test_claim_task_returns_destination_url_from_join(app_db: Database) -> None:
    """``claim_task`` joins the destination catalogue and surfaces the URL."""
    region_id = await _seed_region()
    await _seed_probe("probe-dest", region_id)
    conn = await asyncpg.connect(_test_dsn())
    try:
        destination_row = await conn.fetchrow(
            """
            INSERT INTO destinations (name, url)
            VALUES ($1, $2)
            RETURNING id
            """,
            "Cloudflare",
            "https://speed.example.com/__down?bytes=100000000",
        )
        destination_id = int(destination_row["id"])
        await conn.execute(
            "INSERT INTO tasks (link, destination_id) VALUES ($1, $2)",
            VALID_LINK,
            destination_id,
        )
    finally:
        await conn.close()

    async with app_db.pool.acquire() as conn:
        claimed = await claim_task(conn, probe_token="probe-dest")

    assert claimed is not None
    assert claimed.destination_url == "https://speed.example.com/__down?bytes=100000000"


async def test_claim_task_returns_none_destination_when_task_has_no_destination(
    app_db: Database,
) -> None:
    """Tasks without a destination yield ``destination_url=None`` for the probe."""
    region_id = await _seed_region()
    await _seed_probe("probe-no-dest", region_id)
    await _insert_task(VALID_LINK)

    async with app_db.pool.acquire() as conn:
        claimed = await claim_task(conn, probe_token="probe-no-dest")

    assert claimed is not None
    assert claimed.destination_url is None


async def test_dispatch_to_probe_attaches_destination_url(app_db: Database) -> None:
    """The dispatched ``task.assigned`` envelope carries the destination URL."""
    region_id = await _seed_region()
    await _seed_probe("probe-attach", region_id)
    conn = await asyncpg.connect(_test_dsn())
    try:
        destination_row = await conn.fetchrow(
            """
            INSERT INTO destinations (name, url)
            VALUES ($1, $2)
            RETURNING id
            """,
            "Cloudflare",
            "https://speed.example.com/__down?bytes=100000000",
        )
        destination_id = int(destination_row["id"])
        await conn.execute(
            "INSERT INTO tasks (link, destination_id) VALUES ($1, $2)",
            VALID_LINK,
            destination_id,
        )
    finally:
        await conn.close()

    hub = Hub()
    captured: list[dict[str, Any]] = []

    async def fake_send(token: str, message: dict[str, Any]) -> bool:
        _ = token
        captured.append(message)
        return True

    hub.send = fake_send  # type: ignore[method-assign]

    async with app_db.pool.acquire():
        dispatched = await dispatch_to_probe(app_db, hub, "probe-attach")

    assert dispatched is True
    assert len(captured) == 1
    message = captured[0]
    assert message["type"] == "task.assigned"
    assert message["destination_url"] == "https://speed.example.com/__down?bytes=100000000"


async def test_claim_task_carries_protocol(app_db: Database) -> None:
    """``claim_task`` surfaces the row's protocol so the probe can branch."""
    region_id = await _seed_region()
    await _seed_probe("probe-proto", region_id)
    await _insert_task(VALID_LINK)
    awg_task = await _insert_task(VALID_AWG_LINK, protocol="amneziawg")

    async with app_db.pool.acquire() as conn:
        first = await claim_task(conn, probe_token="probe-proto")
        second = await claim_task(conn, probe_token="probe-proto")

    assert first is not None and first.protocol == "vless"
    assert second is not None and second.id == awg_task
    assert second.protocol == "amneziawg"
    assert second.link == VALID_AWG_LINK


async def test_dispatch_to_probe_sends_protocol(app_db: Database) -> None:
    """The dispatched envelope carries the protocol of the claimed task."""
    region_id = await _seed_region()
    await _seed_probe("probe-envelope", region_id)
    await _insert_task(VALID_AWG_LINK, protocol="amneziawg")

    hub = Hub()
    captured: list[dict[str, Any]] = []

    async def fake_send(token: str, message: dict[str, Any]) -> bool:
        _ = token
        captured.append(message)
        return True

    hub.send = fake_send  # type: ignore[method-assign]

    async with app_db.pool.acquire():
        dispatched = await dispatch_to_probe(app_db, hub, "probe-envelope")

    assert dispatched is True
    assert captured[0]["protocol"] == "amneziawg"


async def test_dispatch_to_probe_defaults_to_vless_protocol(app_db: Database) -> None:
    """Pre-AmneziaWG rows keep the ``vless`` default in the envelope."""
    region_id = await _seed_region()
    await _seed_probe("probe-default", region_id)
    await _insert_task(VALID_LINK)

    hub = Hub()
    captured: list[dict[str, Any]] = []

    async def fake_send(token: str, message: dict[str, Any]) -> bool:
        _ = token
        captured.append(message)
        return True

    hub.send = fake_send  # type: ignore[method-assign]

    async with app_db.pool.acquire():
        await dispatch_to_probe(app_db, hub, "probe-default")

    assert captured[0]["protocol"] == "vless"


async def test_record_result_completes_task_for_unsupported_protocol(
    app_db: Database,
) -> None:
    """An old probe that cannot dial AmneziaWG still finishes the task.

    Such a probe reports ``xray_start_failed`` instead of a speed; the
    task must reach a terminal state rather than stay ``running`` until
    the sweeper recycles it forever.
    """
    region_id = await _seed_region()
    probe_token = "probe-legacy"
    probe_id = await _seed_probe(probe_token, region_id)
    task_id = await _insert_task(VALID_AWG_LINK, protocol="amneziawg")

    async with app_db.pool.acquire() as conn:
        await claim_task(conn, probe_token=probe_token)
        await record_result(
            conn,
            task_id=task_id,
            probe_id=probe_id,
            region_id=region_id,
            speed_mbps=None,
            error="xray_start_failed: not a vless URI",
            raw={"error": "xray_start_failed"},
        )
        completed = await check_completion(conn, task_id)
        row = await conn.fetchrow(
            "SELECT status, completed_at FROM tasks WHERE id = $1",
            task_id,
        )

    assert completed is True
    assert row["status"] == "completed"
    assert row["completed_at"] is not None


__all__ = [
    "test_claim_task_returns_none_when_queue_is_empty",
    "test_claim_task_picks_oldest_pending_task",
    "test_claim_task_filters_by_probe_tokens_whitelist",
    "test_claim_task_marks_running_with_assigned_to",
    "test_claim_task_returns_destination_url_from_join",
    "test_claim_task_returns_none_destination_when_task_has_no_destination",
    "test_dispatch_distributes_tasks_to_probes_without_duplicates",
    "test_dispatch_to_probe_attaches_destination_url",
    "test_check_completion_marks_single_probe_task_completed",
    "test_check_completion_resets_multi_probe_task_to_pending",
    "test_sweep_resets_stale_running_tasks",
    "test_sweep_leaves_recent_running_tasks_alone",
    "test_record_result_overwrites_existing_row_for_same_probe",
    "test_protocol_version_constant_matches_spec",
    "test_claimed_task_is_a_dataclass_with_id_and_link",
    "test_claim_task_carries_protocol",
    "test_dispatch_to_probe_sends_protocol",
    "test_dispatch_to_probe_defaults_to_vless_protocol",
    "test_record_result_completes_task_for_unsupported_protocol",
]
