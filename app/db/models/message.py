"""One-to-one chat message between two doctors.

A message exists independent of any conversations table — a "conversation" is
defined ad-hoc as "all messages where (sender, recipient) is a given pair".
``read_at`` is set when the recipient fetches the thread (or the WS receiver
acknowledges the message), and is the basis for the unread-count badge.

When ``share_id`` is set the message is a *share attachment*: the chat bubble
renders a card with study/series metadata + Import button.  ``body`` becomes
the optional caption the sender wrote alongside the share.  When ``share_id``
is NULL the message is a plain text bubble (the original behaviour).

When ``voice_object_key`` is set the message is a *voice note*: the bubble
renders an audio player fed by a presigned GET minted on read.  The audio bytes
live in the ``voice-messages`` MinIO bucket and never touch Postgres.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.db.models.share import Share


class Message(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "messages"
    __table_args__ = (
        CheckConstraint(
            "char_length(body) BETWEEN 0 AND 4000",
            name="ck_messages_body_length",
        ),
        CheckConstraint("sender_id <> recipient_id", name="ck_messages_no_self_send"),
        # A half-written voice note would render as a player with no source, so
        # the four columns stand or fall together.
        CheckConstraint(
            "(voice_object_key IS NULL AND voice_mime_type IS NULL "
            "AND voice_duration_ms IS NULL AND voice_size_bytes IS NULL) "
            "OR (voice_object_key IS NOT NULL AND voice_mime_type IS NOT NULL "
            "AND voice_duration_ms IS NOT NULL AND voice_size_bytes IS NOT NULL)",
            name="ck_messages_voice_complete",
        ),
        CheckConstraint(
            "voice_duration_ms IS NULL OR voice_duration_ms > 0",
            name="ck_messages_voice_duration_positive",
        ),
        Index("ix_messages_recipient_unread", "recipient_id", "read_at"),
        Index(
            "ix_messages_thread_sr",
            "sender_id",
            "recipient_id",
            "sent_at",
        ),
        Index(
            "ix_messages_thread_rs",
            "recipient_id",
            "sender_id",
            "sent_at",
        ),
    )

    sender_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    recipient_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    # NULL share_id → plain text message; body has the user's text.
    # Set share_id → share attachment; body is optional caption (may be empty).
    body: Mapped[str] = mapped_column(Text, nullable=False, default="")
    share_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("shares.id", ondelete="CASCADE"),
        nullable=True,
    )
    # Object key in the audio bucket; NULL for every non-voice message.
    voice_object_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    voice_mime_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    voice_duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    voice_size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sent_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # lazy='selectin' fires a secondary async-safe SELECT for the share when
    # Message objects are loaded — 'joined' triggers lazy-loading on attribute
    # access which raises MissingGreenlet in async SQLAlchemy contexts.
    share: Mapped[Share | None] = relationship("Share", lazy="selectin", foreign_keys=[share_id])
