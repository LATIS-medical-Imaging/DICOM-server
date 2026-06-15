"""Polymorphic-but-typed share: exactly one of (study_id, series_id, instance_id).

A Share row grants ``grantee_id`` access to a study/series/instance owned by
``grantor_id``.  Newly created shares are PENDING — the grantee must accept
them (flip to ACTIVE) before they appear in their sidebar.  Visibility queries
(``StudyService._active_share_filter``) only consider ACTIVE shares.

Re-shares form a tree via ``parent_share_id``: when a MANAGE grantee re-shares
to a third party, the child share points at the parent.  Revoking any share
cascades REVOKED down the tree atomically, so losing access at any link
removes downstream visibility too.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, func
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.db.models.instance import Instance
    from app.db.models.series import Series
    from app.db.models.study import Study


class SharePermission(str, enum.Enum):
    VIEW = "view"
    ANNOTATE = "annotate"
    MANAGE = "manage"

    @classmethod
    def rank(cls, perm: str) -> int:
        _rank = {cls.VIEW: 1, cls.ANNOTATE: 2, cls.MANAGE: 3}
        return _rank.get(perm, 0)  # type: ignore[call-overload, no-any-return]


class ShareStatus(str, enum.Enum):
    PENDING = "pending"
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

    # Re-share lineage.  NULL for shares created by the resource owner; set to
    # the re-sharer's own incoming share when a MANAGE grantee re-shares.
    parent_share_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("shares.id", ondelete="CASCADE"),
        nullable=True,
    )

    permission: Mapped[SharePermission] = mapped_column(
        SAEnum(
            SharePermission,
            native_enum=False,
            length=20,
            name="share_permission",
            values_callable=lambda x: [e.value for e in x],
        ),
        nullable=False,
        default=SharePermission.VIEW,
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # Default PENDING — receiver must Accept (`POST /shares/{id}/accept`)
    # before the share grants visibility.
    status: Mapped[ShareStatus] = mapped_column(
        SAEnum(
            ShareStatus,
            native_enum=False,
            length=20,
            name="share_status",
            values_callable=lambda x: [e.value for e in x],
        ),
        nullable=False,
        default=ShareStatus.PENDING,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    study: Mapped[Study | None] = relationship(back_populates="shares", foreign_keys=[study_id])
    series: Mapped[Series | None] = relationship(back_populates="shares", foreign_keys=[series_id])
    instance: Mapped[Instance | None] = relationship(
        back_populates="shares", foreign_keys=[instance_id]
    )

    parent: Mapped[Share | None] = relationship(
        "Share",
        remote_side="Share.id",
        back_populates="children",
        foreign_keys=[parent_share_id],
    )
    children: Mapped[list[Share]] = relationship(
        "Share",
        back_populates="parent",
        cascade="all, delete-orphan",
        foreign_keys=[parent_share_id],
    )
