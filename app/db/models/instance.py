"""DICOM Instance — a single image file, row per .dcm."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    SmallInteger,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from decimal import Decimal

    from app.db.models.annotation import Annotation
    from app.db.models.series import Series
    from app.db.models.share import Share


class Instance(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "instances"

    series_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("series.id", ondelete="CASCADE"),
        nullable=False,
    )

    sop_instance_uid: Mapped[str] = mapped_column(String(128), nullable=False)
    sop_class_uid: Mapped[str | None] = mapped_column(String(128), nullable=True)
    instance_number: Mapped[int | None] = mapped_column(Integer, nullable=True)

    rows: Mapped[int | None] = mapped_column(Integer, nullable=True)
    columns: Mapped[int | None] = mapped_column(Integer, nullable=True)
    bits_allocated: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    bits_stored: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    pixel_representation: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    number_of_frames: Mapped[int | None] = mapped_column(Integer, nullable=True, default=1)

    window_center: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), nullable=True)
    window_width: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), nullable=True)
    rescale_intercept: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), nullable=True)
    rescale_slope: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), nullable=True)

    image_position_patient: Mapped[str | None] = mapped_column(String(200), nullable=True)
    image_orientation_patient: Mapped[str | None] = mapped_column(String(200), nullable=True)
    transfer_syntax_uid: Mapped[str | None] = mapped_column(String(128), nullable=True)

    file_path: Mapped[str] = mapped_column(String(500), nullable=False)
    file_size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    content_type: Mapped[str] = mapped_column(
        String(50), nullable=False, default="application/dicom"
    )
    checksum_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    series: Mapped[Series] = relationship(back_populates="instances")
    annotations: Mapped[list[Annotation]] = relationship(
        back_populates="instance", cascade="all, delete-orphan"
    )
    shares: Mapped[list[Share]] = relationship(
        back_populates="instance",
        cascade="all, delete-orphan",
        foreign_keys="Share.instance_id",
    )
