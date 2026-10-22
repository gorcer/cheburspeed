"""Task ORM model."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from xspeed_server.models.base import Base


class Task(Base):
    """Speedtest task scheduled for execution by probes."""

    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(primary_key=True)
    link: Mapped[str] = mapped_column(Text, nullable=False)
    protocol: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        server_default="vless",
    )
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        server_default="pending",
    )
    probe_tokens: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    destination_id: Mapped[int | None] = mapped_column(
        ForeignKey("destinations.id", ondelete="RESTRICT"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    assigned_to: Mapped[str | None] = mapped_column(String(128), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'running', 'completed', 'failed')",
            name="ck_tasks_status",
        ),
        CheckConstraint(
            "protocol IN ('vless', 'amneziawg')",
            name="ck_tasks_protocol",
        ),
        Index("ix_tasks_status_created_at", "status", "created_at"),
        Index("ix_tasks_status_started_at", "status", "started_at"),
    )

    def __repr__(self) -> str:
        return f"Task(id={self.id!r}, status={self.status!r})"
