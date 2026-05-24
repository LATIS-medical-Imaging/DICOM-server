"""DICOM Series — a contiguous set of instances (slices) within a study.

A Series row also represents a "Phase": a derived branch of a parent series
that holds a doctor's saved modifications (filtered instances + annotations).
Phases are distinguished by ``parent_series_id IS NOT NULL`` and carry an
``owner_id`` pointing at the user who saved them.  Originals keep both fields
``NULL`` — visibility for originals comes from the parent study's ownership /
shares model.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, ForeignKey, Integer, Numeric, String
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.db.models.instance import Instance
    from app.db.models.share import Share
    from app.db.models.study import Study
    from app.db.models.user import User


class Series(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "series"

    study_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("studies.id", ondelete="CASCADE"),
        nullable=False,
    )

    # Phase branching: NULL = original DICOM-ingested series; non-NULL = a phase
    # derived from that parent.  ON DELETE CASCADE so deleting the original
    # cleans up all phases that hung off it.
    parent_series_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("series.id", ondelete="CASCADE"),
        nullable=True,
    )

    # The doctor who saved this phase.  NULL for originals.  SET NULL on user
    # delete so the phase isn't wiped — its ownership just becomes orphaned.
    owner_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
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

    # Self-referential: a phase points at its parent; an original lists its phases.
    parent: Mapped[Series | None] = relationship(
        "Series",
        remote_side="Series.id",
        back_populates="phases",
        foreign_keys=[parent_series_id],
    )
    phases: Mapped[list[Series]] = relationship(
        "Series",
        back_populates="parent",
        cascade="all, delete-orphan",
        foreign_keys=[parent_series_id],
    )

    owner: Mapped[User | None] = relationship("User", foreign_keys=[owner_id])
