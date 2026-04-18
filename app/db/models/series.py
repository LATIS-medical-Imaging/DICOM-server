"""DICOM Series — a contiguous set of instances (slices) within a study."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, ForeignKey, Integer, Numeric, String
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from decimal import Decimal

    from app.db.models.instance import Instance
    from app.db.models.share import Share
    from app.db.models.study import Study


class Series(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "series"

    study_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("studies.id", ondelete="CASCADE"),
        nullable=False,
    )

    series_instance_uid: Mapped[str] = mapped_column(String(128), nullable=False)
    series_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    modality: Mapped[str] = mapped_column(String(16), nullable=False)
    series_description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    body_part_examined: Mapped[str | None] = mapped_column(String(64), nullable=True)
    patient_position: Mapped[str | None] = mapped_column(String(16), nullable=True)
    protocol_name: Mapped[str | None] = mapped_column(String(200), nullable=True)

    slice_thickness: Mapped[Decimal | None] = mapped_column(Numeric(10, 4), nullable=True)
    spacing_between_slices: Mapped[Decimal | None] = mapped_column(Numeric(10, 4), nullable=True)
    pixel_spacing: Mapped[str | None] = mapped_column(String(64), nullable=True)

    instance_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    storage_path: Mapped[str] = mapped_column(String(500), nullable=False)

    study: Mapped[Study] = relationship(back_populates="series")
    instances: Mapped[list[Instance]] = relationship(
        back_populates="series", cascade="all, delete-orphan"
    )
    shares: Mapped[list[Share]] = relationship(
        back_populates="series",
        cascade="all, delete-orphan",
        foreign_keys="Share.series_id",
    )
