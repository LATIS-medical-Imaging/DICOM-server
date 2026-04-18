"""Patient demographics, created by a doctor before uploading a study."""

from __future__ import annotations

import uuid
from datetime import date
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, Date, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.db.models.study import Study
    from app.db.models.user import User


class Patient(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "patients"
    __table_args__ = (CheckConstraint("sex IN ('M', 'F', 'O')", name="ck_patients_sex"),)

    created_by: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )

    patient_name: Mapped[str] = mapped_column(String(300), nullable=False)
    patient_id: Mapped[str] = mapped_column(String(64), nullable=False)
    birth_date: Mapped[date] = mapped_column(Date, nullable=False)
    sex: Mapped[str] = mapped_column(String(1), nullable=False)

    creator: Mapped[User] = relationship(back_populates="patients")
    studies: Mapped[list[Study]] = relationship(back_populates="patient")
