"""destinations table + tasks.destination_id

Revision ID: 0005_destinations
Revises: 0004_ws_dispatch
Create Date: 2026-10-16 12:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_destinations"
down_revision: str | None = "0004_ws_dispatch"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "destinations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("url", sa.String(length=2048), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "url LIKE 'http://%' OR url LIKE 'https://%'",
            name="ck_destinations_url_scheme",
        ),
        sa.UniqueConstraint("name", name="uq_destinations_name"),
    )
    op.create_index("ix_destinations_name", "destinations", ["name"], unique=True)

    op.add_column(
        "tasks",
        sa.Column(
            "destination_id",
            sa.Integer(),
            sa.ForeignKey("destinations.id", ondelete="RESTRICT"),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("tasks", "destination_id")
    op.drop_index("ix_destinations_name", table_name="destinations")
    op.drop_table("destinations")
