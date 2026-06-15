"""User model — doctors and administrators."""

from __future__ import annotations

import enum
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, String
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, SoftDeleteMixin, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.db.models.annotation import Annotation
    from app.db.models.patient import Patient
    from app.db.models.study import Study
    from app.db.models.upload_job import UploadJob
    from app.db.models.user_session import UserSession


class UserRole(str, enum.Enum):
    ADMIN = "admin"
    DOCTOR = "doctor"


class User(Base, UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(320), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    email_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    first_name: Mapped[str] = mapped_column(String(100), nullable=False)
    last_name: Mapped[str] = mapped_column(String(100), nullable=False)
    title: Mapped[str | None] = mapped_column(String(20), nullable=True)
    specialty: Mapped[str | None] = mapped_column(String(100), nullable=True)
    license_number: Mapped[str | None] = mapped_column(String(50), nullable=True)
    institution: Mapped[str | None] = mapped_column(String(200), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(20), nullable=True)
    avatar_url: Mapped[str | None] = mapped_column(String(500), nullable=True)

    role: Mapped[UserRole] = mapped_column(
        SAEnum(
            UserRole,
            native_enum=False,
            length=20,
            name="user_role",
            values_callable=lambda x: [e.value for e in x],
        ),
        nullable=False,
        default=UserRole.DOCTOR,
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Relationships
    sessions: Mapped[list[UserSession]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    patients: Mapped[list[Patient]] = relationship(back_populates="creator")
    studies: Mapped[list[Study]] = relationship(back_populates="owner")
    annotations: Mapped[list[Annotation]] = relationship(back_populates="user")
    upload_jobs: Mapped[list[UploadJob]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<User id={self.id} email={self.email!r} role={self.role!r}>"
