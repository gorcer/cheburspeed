"""Probe ORM model."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column

from xspeed_server.models.base import Base


class Probe(Base):
    """Probe (vless speedtest agent) registered in a region."""

    __tablename__ = "probes"

    id: Mapped[int] = mapped_column(primary_key=True)
    alias: Mapped[str] = mapped_column(String(255), nullable=False, default="", server_default="")
    token: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    region_id: Mapped[int] = mapped_column(
        ForeignKey("regions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    is_public: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="true",
    )
    last_seen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    __table_args__ = (Index("ix_probes_last_seen_at", "last_seen_at"),)

    def __repr__(self) -> str:
        return f"Probe(id={self.id!r}, alias={self.alias!r}, region_id={self.region_id!r})"
