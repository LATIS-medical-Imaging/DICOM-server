"""Polymorphic-but-typed share: exactly one of (study_id, series_id, instance_id)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.db.models.instance import Instance
    from app.db.models.series import Series
    from app.db.models.study import Study


class SharePermission:
    VIEW = "view"
    ANNOTATE = "annotate"
    MANAGE = "manage"


class ShareStatus:
    ACTIVE = "active"
    REVOKED = "revoked"
    EXPIRED = "expired"


class Share(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "shares"
    __table_args__ = (
        CheckConstraint(
            "(study_id IS NOT NULL)::int + (series_id IS NOT NULL)::int "
            "+ (instance_id IS NOT NULL)::int = 1",
            name="exactly_one_resource",
        ),
        CheckConstraint(
            "permission IN ('view', 'annotate', 'manage')",
            name="ck_shares_permission",
        ),
        CheckConstraint(
            "status IN ('active', 'revoked', 'expired')",
            name="ck_shares_status",
        ),
    )

    grantor_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    grantee_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )

    study_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("studies.id", ondelete="CASCADE"),
        nullable=True,
    )
    series_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("series.id", ondelete="CASCADE"),
        nullable=True,
    )
    instance_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("instances.id", ondelete="CASCADE"),
        nullable=True,
    )

    permission: Mapped[str] = mapped_column(String(20), nullable=False, default=SharePermission.VIEW)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=ShareStatus.ACTIVE)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    study: Mapped["Study | None"] = relationship(back_populates="shares", foreign_keys=[study_id])
    series: Mapped["Series | None"] = relationship(back_populates="shares", foreign_keys=[series_id])
    instance: Mapped["Instance | None"] = relationship(
        back_populates="shares", foreign_keys=[instance_id]
    )
