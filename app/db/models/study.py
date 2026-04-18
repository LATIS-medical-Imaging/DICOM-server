"""DICOM Study — top of the Study > Series > Instance hierarchy."""

from __future__ import annotations

import uuid
from datetime import date, time
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    ForeignKey,
    Integer,
    String,
    Time,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, SoftDeleteMixin, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.db.models.patient import Patient
    from app.db.models.series import Series
    from app.db.models.share import Share
    from app.db.models.user import User


class StudyStatus:
    PROCESSING = "processing"
    READY = "ready"
    ERROR = "error"
    ARCHIVED = "archived"


class Study(Base, UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "studies"
    __table_args__ = (
        CheckConstraint(
            "status IN ('processing', 'ready', 'error', 'archived')",
            name="ck_studies_status",
        ),
    )

    owner_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    patient_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("patients.id", ondelete="RESTRICT"),
        nullable=False,
    )

    study_instance_uid: Mapped[str] = mapped_column(String(128), nullable=False)
    accession_number: Mapped[str | None] = mapped_column(String(64), nullable=True)
    study_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    study_time: Mapped[time | None] = mapped_column(Time, nullable=True)
    study_description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    modality: Mapped[str | None] = mapped_column(String(16), nullable=True)
    referring_physician: Mapped[str | None] = mapped_column(String(200), nullable=True)
    institution_name: Mapped[str | None] = mapped_column(String(200), nullable=True)

    total_series_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_instance_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    storage_path: Mapped[str] = mapped_column(String(500), nullable=False)

    status: Mapped[str] = mapped_column(String(20), nullable=False, default=StudyStatus.PROCESSING)

    owner: Mapped[User] = relationship(back_populates="studies")
    patient: Mapped[Patient] = relationship(back_populates="studies")
    series: Mapped[list[Series]] = relationship(
        back_populates="study", cascade="all, delete-orphan"
    )
    shares: Mapped[list[Share]] = relationship(
        back_populates="study",
        cascade="all, delete-orphan",
        foreign_keys="Share.study_id",
    )
