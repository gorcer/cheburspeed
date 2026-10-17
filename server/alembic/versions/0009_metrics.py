"""metrics journal: per-client speedtest measurements and load samples

Revision ID: 0009_metrics
Revises: 0008_seed_probe
Create Date: 2026-10-17 12:00:00.000000

Adds the ``metrics`` table backing the admin «Метрики» journal (DAT-446).
Each row is a single client-side sample taken during a speedtest probe:

* ``client_ip`` — the originating IP seen by the probe (always set).
* ``client_isp`` — optional provider label supplied by the probe or an
  external enricher; the server stores whatever value arrives and does
  not auto-resolve it.
* ``client_lat``/``client_lng`` — optional geographic coordinates of the
  client. The admin UI turns them into a clickable globe icon that
  opens Google Maps; a NULL pair disables the icon.
* ``server_id`` — references ``probes.id``. We deliberately reuse the
  existing ``probes`` table as the «сервер» directory instead of
  introducing a parallel ``servers`` catalogue; ``probes`` already
  carries alias/region/IP/last_seen for the same physical nodes that
  emit speedtest results.
* ``ip`` — single IP captured for the test (RU/EU separation was
  dropped after the author's clarification).
* ``metric_alias`` — short identifier of the metric being recorded
  (e.g. ``download_mbps``, ``load_avg``, ``jitter_ms``).
* ``value`` — optional numeric sample; NULL when the metric is purely
  event-shaped (e.g. ``online``).
* ``created_at`` — server-side timestamp with timezone, defaulting to
  ``now()`` so the probe never has to provide one.

Three indexes cover the access patterns of the admin journal: an
ordering index on ``created_at`` for the paginated list, a
``(server_id, created_at)`` index for per-probe filtering, and a
``(metric_alias, created_at)`` index for filtering by metric type.
``ON DELETE RESTRICT`` on ``server_id`` mirrors the policy used by
``task_results`` and ``destinations`` — an admin probe cannot be
deleted while metrics reference it (the journal must outlive the
probe lifetime to keep history intact).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009_metrics"
down_revision: str | None = "0008_seed_probe"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "metrics",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("client_ip", sa.String(length=64), nullable=False),
        sa.Column("client_isp", sa.String(length=255), nullable=True),
        sa.Column("client_lat", sa.Float(), nullable=True),
        sa.Column("client_lng", sa.Float(), nullable=True),
        sa.Column(
            "server_id",
            sa.Integer(),
            sa.ForeignKey("probes.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("ip", sa.String(length=64), nullable=True),
        sa.Column("metric_alias", sa.String(length=128), nullable=False),
        sa.Column("value", sa.Float(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_metrics_created_at",
        "metrics",
        ["created_at"],
    )
    op.create_index(
        "ix_metrics_server_id_created_at",
        "metrics",
        ["server_id", "created_at"],
    )
    op.create_index(
        "ix_metrics_metric_alias_created_at",
        "metrics",
        ["metric_alias", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_metrics_metric_alias_created_at", table_name="metrics")
    op.drop_index("ix_metrics_server_id_created_at", table_name="metrics")
    op.drop_index("ix_metrics_created_at", table_name="metrics")
    op.drop_table("metrics")
