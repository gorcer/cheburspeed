"""seed destinations: cloudflare + dataforest (CheburSpeed static payload)

Revision ID: 0006_seed_destinations
Revises: 0005_destinations
Create Date: 2026-10-16 12:30:00.000000

Inserts the two default speedtest destinations so a fresh deployment has
something to pick in the dropdown before the operator customises the
catalog via ``/api/v1/admin/destinations``.

* ``cloudflare`` — the legacy 10 MB download endpoint that used to live
  in ``PROBE_SPEEDTEST_URL`` before destinations were introduced.
* ``dataforest`` — the operator's own static speedtest file served by
  the dataforest.app nginx at ``/blob`` (100 MB random bytes, plain
  octet-stream, range-supporting). dataforest.app is a public
  HTTPS endpoint that the probe can reach from any network without
  the help of a tunnel or port-forward on the residential PPTP
  link. The URL can be overridden at upgrade time by exporting
  ``XSPEED_SEED_DATAFOREST_URL`` (e.g. a Cloudflare-fronted prod
  domain).

Both inserts are idempotent (``ON CONFLICT (name) DO NOTHING``) so
re-running the migration or applying it after the operator renamed
either row is a no-op.
"""

from __future__ import annotations

import os
from collections.abc import Sequence

from alembic import op

revision: str = "0006_seed_destinations"
down_revision: str | None = "0005_destinations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


CLOUDFLARE_URL: str = "https://speed.cloudflare.com/__down?bytes=10000000"
DATAFOREST_URL_DEFAULT: str = "https://dataforest.app/blob"


_SEEDS: tuple[tuple[str, str], ...] = (
    ("cloudflare", CLOUDFLARE_URL),
    (
        "dataforest",
        os.environ.get("XSPEED_SEED_DATAFOREST_URL") or DATAFOREST_URL_DEFAULT,
    ),
)


def upgrade() -> None:
    bind = op.get_bind()
    for name, url in _SEEDS:
        bind.exec_driver_sql(
            "INSERT INTO destinations (name, url) VALUES ($1, $2) "
            "ON CONFLICT (name) DO UPDATE SET url = EXCLUDED.url",
            (name, url),
        )


def downgrade() -> None:
    bind = op.get_bind()
    bind.exec_driver_sql(
        "DELETE FROM destinations WHERE name IN ('cloudflare', 'dataforest')"
    )