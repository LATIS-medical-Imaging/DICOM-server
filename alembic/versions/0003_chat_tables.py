"""Chat module — friendships + messages.

Adds the two tables that power doctor-to-doctor real-time chat:

* ``friendships`` — single row per pair (canonical ``user_a_id < user_b_id``),
  status enum ``pending`` / ``accepted``.  Rejection and unfriend both delete.
* ``messages`` — 1:1 message history, with ``read_at`` driving the unread badge.

Revision ID: 0003
Revises: 0002
Create Date: 2026-05-21
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "friendships",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("user_a_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_b_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("requested_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.ForeignKeyConstraint(["user_a_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_b_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["requested_by"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_a_id", "user_b_id", name="uq_friendships_pair"),
        sa.CheckConstraint("user_a_id < user_b_id", name="ck_friendships_canonical_order"),
        sa.CheckConstraint(
            "status IN ('pending', 'accepted')",
            name="ck_friendships_status",
        ),
    )
    op.create_index("ix_friendships_user_a_id", "friendships", ["user_a_id"])
    op.create_index("ix_friendships_user_b_id", "friendships", ["user_b_id"])

    op.create_table(
        "messages",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("sender_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("recipient_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column(
            "sent_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["sender_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["recipient_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "char_length(body) BETWEEN 1 AND 4000",
            name="ck_messages_body_length",
        ),
        sa.CheckConstraint("sender_id <> recipient_id", name="ck_messages_no_self_send"),
    )
    op.create_index(
        "ix_messages_recipient_unread", "messages", ["recipient_id", "read_at"]
    )
    op.create_index(
        "ix_messages_thread_sr", "messages", ["sender_id", "recipient_id", "sent_at"]
    )
    op.create_index(
        "ix_messages_thread_rs", "messages", ["recipient_id", "sender_id", "sent_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_messages_thread_rs", table_name="messages")
    op.drop_index("ix_messages_thread_sr", table_name="messages")
    op.drop_index("ix_messages_recipient_unread", table_name="messages")
    op.drop_table("messages")

    op.drop_index("ix_friendships_user_b_id", table_name="friendships")
    op.drop_index("ix_friendships_user_a_id", table_name="friendships")
    op.drop_table("friendships")
