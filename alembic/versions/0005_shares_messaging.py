"""Shares as chat attachments + re-share tree + accept lifecycle.

Three columns are introduced:

* ``messages.share_id`` — nullable FK to ``shares.id`` (CASCADE).  When set,
  the chat bubble renders a share card with study/series metadata; ``body``
  becomes the optional caption.  When NULL the message is a plain text bubble
  (backward-compatible with all existing rows).
* ``shares.parent_share_id`` — nullable self-FK (CASCADE).  Set when a MANAGE
  grantee re-shares; the child points at the parent so revoking any link
  cascade-revokes downstream.
* ``shares.accepted_at`` — nullable timestamp set when the grantee accepts a
  PENDING share (transition to ACTIVE).

The existing ``shares.status`` CHECK constraint allowed only
``active / revoked / expired``; we replace it to permit ``pending`` (the new
default for fresh shares).  ``messages.body`` had a CHECK enforcing length 1+
which now relaxes to 0+ so share attachments may have an empty caption.

Partial indexes on both new FK columns — null is the dominant value, so
indexing nulls would just waste space.

Revision ID: 0005
Revises: 0004
Create Date: 2026-05-25
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── messages.share_id ────────────────────────────────────────────────
    op.add_column(
        "messages",
        sa.Column(
            "share_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.create_foreign_key(
        "fk_messages_share_id",
        "messages",
        "shares",
        ["share_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index(
        "ix_messages_share_id",
        "messages",
        ["share_id"],
        postgresql_where=sa.text("share_id IS NOT NULL"),
    )

    # ── relax messages.body length check so share attachments can have ──
    #    an empty caption (the share card carries the meaning).
    op.drop_constraint("ck_messages_body_length", "messages", type_="check")
    op.create_check_constraint(
        "ck_messages_body_length",
        "messages",
        "char_length(body) BETWEEN 0 AND 4000",
    )

    # ── shares.parent_share_id ───────────────────────────────────────────
    op.add_column(
        "shares",
        sa.Column(
            "parent_share_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.create_foreign_key(
        "fk_shares_parent_share_id",
        "shares",
        "shares",
        ["parent_share_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index(
        "ix_shares_parent_share_id",
        "shares",
        ["parent_share_id"],
        postgresql_where=sa.text("parent_share_id IS NOT NULL"),
    )

    # ── shares.accepted_at ───────────────────────────────────────────────
    op.add_column(
        "shares",
        sa.Column(
            "accepted_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )

    # ── allow PENDING in the status CHECK ────────────────────────────────
    op.drop_constraint("ck_shares_status", "shares", type_="check")
    op.create_check_constraint(
        "ck_shares_status",
        "shares",
        "status IN ('pending', 'active', 'revoked', 'expired')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_shares_status", "shares", type_="check")
    op.create_check_constraint(
        "ck_shares_status",
        "shares",
        "status IN ('active', 'revoked', 'expired')",
    )
    op.drop_column("shares", "accepted_at")
    op.drop_index("ix_shares_parent_share_id", table_name="shares")
    op.drop_constraint("fk_shares_parent_share_id", "shares", type_="foreignkey")
    op.drop_column("shares", "parent_share_id")
    op.drop_constraint("ck_messages_body_length", "messages", type_="check")
    op.create_check_constraint(
        "ck_messages_body_length",
        "messages",
        "char_length(body) BETWEEN 1 AND 4000",
    )
    op.drop_index("ix_messages_share_id", table_name="messages")
    op.drop_constraint("fk_messages_share_id", "messages", type_="foreignkey")
    op.drop_column("messages", "share_id")
