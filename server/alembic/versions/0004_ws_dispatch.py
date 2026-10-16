"""ws dispatch columns on tasks

Revision ID: 0004_ws_dispatch
Revises: 0003_archive
Create Date: 2026-10-13 12:30:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_ws_dispatch"
down_revision: str | None = "0003_archive"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tasks",
        sa.Column("assigned_to", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "tasks",
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_tasks_status_started_at",
        "tasks",
        ["status", "started_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_tasks_status_started_at", table_name="tasks")
    op.drop_column("tasks", "started_at")
    op.drop_column("tasks", "assigned_to")
