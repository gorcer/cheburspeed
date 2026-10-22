"""tasks.protocol + widen tasks.link for AmneziaWG configs

Revision ID: 0009_task_protocol
Revises: 0008_seed_probe
Create Date: 2026-10-22 07:10:00.000000

Adds ``tasks.protocol`` so a task row remembers whether ``tasks.link``
holds a ``vless://`` URI or a full AmneziaWG INI config, and widens
``tasks.link`` from ``VARCHAR(2048)`` to ``TEXT``.

Both changes must land in one revision: an AmneziaWG config with
several ``[Peer]`` sections easily exceeds 2048 characters, so a probe
that understands ``protocol='amneziawg'`` would hit
``value too long for type character varying(2048)`` on INSERT if the
column stayed narrow. The CHECK constraint keeps the value in the
closed set the API and the probe both branch on, so a typo in a manual
``UPDATE`` fails loudly instead of producing a task no probe can run.

The column is ``NOT NULL DEFAULT 'vless'`` so existing rows — and any
older code path that still INSERTs without naming the column — keep
working unchanged.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0009_task_protocol"
down_revision: str | None = "0008_seed_probe"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE tasks ADD COLUMN protocol VARCHAR(16) NOT NULL DEFAULT 'vless'")
    op.execute(
        "ALTER TABLE tasks ADD CONSTRAINT ck_tasks_protocol "
        "CHECK (protocol IN ('vless', 'amneziawg'))",
    )
    op.execute("ALTER TABLE tasks ALTER COLUMN link TYPE TEXT")


def downgrade() -> None:
    op.execute("ALTER TABLE tasks DROP CONSTRAINT IF EXISTS ck_tasks_protocol")
    op.execute("ALTER TABLE tasks ALTER COLUMN link TYPE VARCHAR(2048)")
    op.execute("ALTER TABLE tasks DROP COLUMN protocol")