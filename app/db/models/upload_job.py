"""Tracks granular upload progress independently of studies.status."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Integer, String, func
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.db.models.user import User


class UploadJobStatus(str, enum.Enum):
    PENDING = "pending"  # job created, worker hasn't picked it up yet
    PROCESSING = "processing"  # worker is actively ingesting (useful for detecting stuck jobs)
    COMPLETED = "completed"
    FAILED = "failed"


class UploadJob(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "upload_jobs"
    __table_args__ = (
        CheckConstraint(
            "job_type IN ('dicom_upload', 'dicom_import', 'bulk_upload')",
            name="ck_upload_jobs_type",
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    study_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("studies.id", ondelete="SET NULL"),
        nullable=True,
    )

    job_type: Mapped[str] = mapped_column(String(20), nullable=False, default="dicom_upload")
    status: Mapped[UploadJobStatus] = mapped_column(
        SAEnum(
            UploadJobStatus,
            native_enum=False,
            length=20,
            name="upload_job_status",
            values_callable=lambda x: [e.value for e in x],
        ),
        nullable=False,
        default=UploadJobStatus.PENDING,
    )

    total_files: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    processed_files: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed_files: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    processed_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)

    errors: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, server_default="[]")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    user: Mapped[User] = relationship(back_populates="upload_jobs")
