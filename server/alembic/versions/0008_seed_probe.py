"""seed probes: bootstrap the first operator-managed probe

Revision ID: 0008_seed_probe
Revises: 0007_seed_regions
Create Date: 2026-10-16 15:00:00.000000

Registers the local Primorsky Krai probe whose token is wired into
``PROBE_TOKEN`` in ``.env`` and shipped by ``./probe.sh``. Without
this row, the WebSocket handshake at ``/ws`` fails with HTTP 403
because the server looks the bearer token up in ``probes.token``
and rejects unknown clients — see
:func:`xspeed_server.app.ws.handlers._authenticate`.

The probe is pinned to the region with ``code = 'PK'`` (the short
``db_code`` for ``RU-PRI`` "Приморский край" seeded by
``0007_seed_regions``). The insert is idempotent
(``ON CONFLICT (token) DO NOTHING``) so re-running the migration or
applying it after the operator deleted and re-registered the probe
is a no-op.
"""

from __future__ import annotations

import os
from collections.abc import Sequence

from alembic import op

revision: str = "0008_seed_probe"
down_revision: str | None = "0007_seed_regions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


PRIMORJE_TOKEN_DEFAULT: str = "FMxc-b2Q9a0AOEY5DyLEKNKupLdjIg_1PjCwSFYkHy0"
PRIMORJE_ALIAS: str = "primorje"
PRIMORJE_REGION_CODE: str = "PK"


def _primorje_token() -> str:
    return os.environ.get("XSPEED_SEED_PRIMORJE_TOKEN") or PRIMORJE_TOKEN_DEFAULT


def upgrade() -> None:
    bind = op.get_bind()
    region_id = bind.exec_driver_sql(
        "SELECT id FROM regions WHERE code = $1",
        (PRIMORJE_REGION_CODE,),
    ).scalar()
    if region_id is None:
        raise RuntimeError(
            f"Region with code {PRIMORJE_REGION_CODE!r} not found; "
            "0007_seed_regions must run first",
        )
    bind.exec_driver_sql(
        """
        INSERT INTO probes (alias, token, region_id, is_public)
        VALUES ($1, $2, $3, true)
        ON CONFLICT (token) DO NOTHING
        """,
        (PRIMORJE_ALIAS, _primorje_token(), int(region_id)),
    )


def downgrade() -> None:
    bind = op.get_bind()
    bind.exec_driver_sql(
        "DELETE FROM probes WHERE token = $1",
        (_primorje_token(),),
    )
