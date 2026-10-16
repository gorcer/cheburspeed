"""task_results_archive table for cold storage

Revision ID: 0003_archive
Revises: 0002_admins
Create Date: 2026-10-13 12:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003_archive"
down_revision: str | None = "0002_admins"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "task_results_archive",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "task_id",
            sa.Integer(),
            sa.ForeignKey("tasks.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "probe_id",
            sa.Integer(),
            sa.ForeignKey("probes.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "region_id",
            sa.Integer(),
            sa.ForeignKey("regions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("speed_mbps", sa.Numeric(precision=10, scale=3), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "raw",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "archived_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_task_results_archive_created_at",
        "task_results_archive",
        ["created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_task_results_archive_created_at",
        table_name="task_results_archive",
    )
    op.drop_table("task_results_archive")
