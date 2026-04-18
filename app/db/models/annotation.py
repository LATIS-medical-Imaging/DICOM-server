"""Annotation — CornerstoneJS payload + extracted measurement scalars."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from sqlalchemy import Boolean, ForeignKey, Numeric, String
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, SoftDeleteMixin, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from decimal import Decimal

    from app.db.models.instance import Instance
    from app.db.models.user import User


class Annotation(Base, UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "annotations"

    instance_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("instances.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )

    cornerstone_uid: Mapped[str | None] = mapped_column(String(128), nullable=True)
    tool_type: Mapped[str] = mapped_column(String(50), nullable=False)

    annotation_data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    viewport_state: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )

    measurement_value: Mapped["Decimal | None"] = mapped_column(Numeric(15, 6), nullable=True)
    measurement_unit: Mapped[str | None] = mapped_column(String(20), nullable=True)
    measurement_area: Mapped["Decimal | None"] = mapped_column(Numeric(15, 6), nullable=True)
    measurement_mean: Mapped["Decimal | None"] = mapped_column(Numeric(15, 6), nullable=True)
    measurement_stddev: Mapped["Decimal | None"] = mapped_column(Numeric(15, 6), nullable=True)

    label: Mapped[str | None] = mapped_column(String(500), nullable=True)
    is_visible: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_locked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    color: Mapped[str | None] = mapped_column(String(7), nullable=True)

    instance: Mapped["Instance"] = relationship(back_populates="annotations")
    user: Mapped["User"] = relationship(back_populates="annotations")
