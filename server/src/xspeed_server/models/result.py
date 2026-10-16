"""TaskResult and TaskResultArchive ORM models."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Index, Numeric, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from xspeed_server.models.base import Base


class TaskResult(Base):
    """Speedtest result produced by a single probe for a single task."""

    __tablename__ = "task_results"

    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"),
        nullable=False,
    )
    probe_id: Mapped[int] = mapped_column(
        ForeignKey("probes.id", ondelete="RESTRICT"),
        nullable=False,
    )
    region_id: Mapped[int] = mapped_column(
        ForeignKey("regions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    speed_mbps: Mapped[Decimal | None] = mapped_column(Numeric(10, 3), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw: Mapped[dict[str, object]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default="{}",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint("task_id", "probe_id", name="uq_task_results_task_probe"),
        Index("ix_task_results_task_id", "task_id"),
    )

    def __repr__(self) -> str:
        return (
            f"TaskResult(id={self.id!r}, task_id={self.task_id!r}, "
            f"probe_id={self.probe_id!r}, speed_mbps={self.speed_mbps!r})"
        )


class TaskResultArchive(Base):
    """Cold storage mirror of task_results; populated by the archive job."""

    __tablename__ = "task_results_archive"

    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(
        ForeignKey("tasks.id", ondelete="RESTRICT"),
        nullable=False,
    )
    probe_id: Mapped[int] = mapped_column(
        ForeignKey("probes.id", ondelete="RESTRICT"),
        nullable=False,
    )
    region_id: Mapped[int] = mapped_column(
        ForeignKey("regions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    speed_mbps: Mapped[Decimal | None] = mapped_column(Numeric(10, 3), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw: Mapped[dict[str, object]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default="{}",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    archived_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    __table_args__ = (Index("ix_task_results_archive_created_at", "created_at"),)

    def __repr__(self) -> str:
        return (
            f"TaskResultArchive(id={self.id!r}, task_id={self.task_id!r}, "
            f"probe_id={self.probe_id!r})"
        )
