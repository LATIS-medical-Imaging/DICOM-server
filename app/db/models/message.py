"""One-to-one chat message between two doctors.

A message exists independent of any conversations table — a "conversation" is
defined ad-hoc as "all messages where (sender, recipient) is a given pair".
``read_at`` is set when the recipient fetches the thread (or the WS receiver
acknowledges the message), and is the basis for the unread-count badge.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Text, func
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDPrimaryKeyMixin


class Message(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "messages"
    __table_args__ = (
        CheckConstraint(
            "char_length(body) BETWEEN 1 AND 4000",
            name="ck_messages_body_length",
        ),
        CheckConstraint("sender_id <> recipient_id", name="ck_messages_no_self_send"),
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
    body: Mapped[str] = mapped_column(Text, nullable=False)
    sent_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
