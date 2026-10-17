"""Metric ORM model."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column

from xspeed_server.models.base import Base


class Metric(Base):
    """Single client-side measurement captured during a speedtest probe.

    The journal holds heterogeneous samples (speed, load, jitter, …)
    keyed by ``metric_alias``; ``value`` may stay NULL when the metric
    is event-shaped rather than numeric. ``server_id`` resolves to
    ``probes.id`` because the codebase reuses the probe table as the
    authoritative «сервер» directory.
    """

    __tablename__ = "metrics"

    id: Mapped[int] = mapped_column(primary_key=True)
    client_ip: Mapped[str] = mapped_column(String(64), nullable=False)
    client_isp: Mapped[str | None] = mapped_column(String(255), nullable=True)
    client_lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    client_lng: Mapped[float | None] = mapped_column(Float, nullable=True)
    server_id: Mapped[int] = mapped_column(
        ForeignKey("probes.id", ondelete="RESTRICT"),
        nullable=False,
    )
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    metric_alias: Mapped[str] = mapped_column(String(128), nullable=False)
    value: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    __table_args__ = (
        Index("ix_metrics_created_at", "created_at"),
        Index("ix_metrics_server_id_created_at", "server_id", "created_at"),
        Index("ix_metrics_metric_alias_created_at", "metric_alias", "created_at"),
    )

    def __repr__(self) -> str:
        return (
            f"Metric(id={self.id!r}, server_id={self.server_id!r}, "
            f"metric_alias={self.metric_alias!r})"
        )
