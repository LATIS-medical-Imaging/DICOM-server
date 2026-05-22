"""Friendship between two doctors — one row per pair, lifecycle in ``status``.

A single relationship table covers the entire lifecycle: an invitation is just a
``pending`` row; acceptance flips ``status`` to ``accepted``; rejection and
unfriending both delete the row.  No separate invitations log is kept — the row
*is* the relationship, in whatever state.

Canonical ordering (``user_a_id < user_b_id``) makes the uniqueness constraint
direction-agnostic: there can only ever be one row for a given pair, regardless
of who initiated.  ``requested_by`` records the inviter so the UI can tell
incoming from outgoing invitations.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    pass


class FriendshipStatus:
    PENDING = "pending"
    ACCEPTED = "accepted"


class Friendship(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "friendships"
    __table_args__ = (
        UniqueConstraint("user_a_id", "user_b_id", name="uq_friendships_pair"),
        CheckConstraint("user_a_id < user_b_id", name="ck_friendships_canonical_order"),
        CheckConstraint(
            "status IN ('pending', 'accepted')",
            name="ck_friendships_status",
        ),
        Index("ix_friendships_user_a_id", "user_a_id"),
        Index("ix_friendships_user_b_id", "user_b_id"),
    )

    user_a_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_b_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    requested_by: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=FriendshipStatus.PENDING
    )
