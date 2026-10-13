"""initial schema: regions, probes, tasks, task_results

Revision ID: 0001_init
Revises:
Create Date: 2026-10-13 12:00:00.000000

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_init"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "regions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("lat", sa.Float(), nullable=True),
        sa.Column("lon", sa.Float(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint("code", name="uq_regions_code"),
    )
    op.create_index("ix_regions_code", "regions", ["code"], unique=True)

    op.create_table(
        "probes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "alias",
            sa.String(length=255),
            nullable=False,
            server_default="",
        ),
        sa.Column("token", sa.String(length=128), nullable=False),
        sa.Column(
            "region_id",
            sa.Integer(),
            sa.ForeignKey("regions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("ip", sa.String(length=64), nullable=True),
        sa.Column(
            "is_public",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint("token", name="uq_probes_token"),
    )
    op.create_index("ix_probes_token", "probes", ["token"], unique=True)
    op.create_index("ix_probes_last_seen_at", "probes", ["last_seen_at"])

    op.create_table(
        "tasks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("link", sa.String(length=2048), nullable=False),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("probe_tokens", postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'completed', 'failed')",
            name="ck_tasks_status",
        ),
    )
    op.create_index("ix_tasks_status_created_at", "tasks", ["status", "created_at"])

    op.create_table(
        "task_results",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "task_id",
            sa.Integer(),
            sa.ForeignKey("tasks.id", ondelete="CASCADE"),
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
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint("task_id", "probe_id", name="uq_task_results_task_probe"),
    )
    op.create_index("ix_task_results_task_id", "task_results", ["task_id"])


def downgrade() -> None:
    op.drop_index("ix_task_results_task_id", table_name="task_results")
    op.drop_table("task_results")
    op.drop_index("ix_tasks_status_created_at", table_name="tasks")
    op.drop_table("tasks")
    op.drop_index("ix_probes_last_seen_at", table_name="probes")
    op.drop_index("ix_probes_token", table_name="probes")
    op.drop_table("probes")
    op.drop_index("ix_regions_code", table_name="regions")
    op.drop_table("regions")