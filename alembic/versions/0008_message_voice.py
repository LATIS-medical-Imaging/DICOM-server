"""Voice notes on chat messages.

The audio lives in the ``voice-messages`` MinIO bucket; only its object key
and the metadata the bubble needs to render a player before the blob loads
(duration, mime type, size) land in Postgres — the same
metadata-in-the-DB / bytes-in-object-storage split the DICOM pipeline uses.

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-06
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: Union[str, None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("messages", sa.Column("voice_object_key", sa.String(length=512), nullable=True))
    op.add_column("messages", sa.Column("voice_mime_type", sa.String(length=100), nullable=True))
    op.add_column("messages", sa.Column("voice_duration_ms", sa.Integer(), nullable=True))
    op.add_column("messages", sa.Column("voice_size_bytes", sa.Integer(), nullable=True))

    op.create_check_constraint(
        "ck_messages_voice_complete",
        "messages",
        "(voice_object_key IS NULL AND voice_mime_type IS NULL "
        "AND voice_duration_ms IS NULL AND voice_size_bytes IS NULL) "
        "OR (voice_object_key IS NOT NULL AND voice_mime_type IS NOT NULL "
        "AND voice_duration_ms IS NOT NULL AND voice_size_bytes IS NOT NULL)",
    )
    op.create_check_constraint(
        "ck_messages_voice_duration_positive",
        "messages",
        "voice_duration_ms IS NULL OR voice_duration_ms > 0",
    )


def downgrade() -> None:
    op.drop_constraint("ck_messages_voice_duration_positive", "messages", type_="check")
    op.drop_constraint("ck_messages_voice_complete", "messages", type_="check")
    op.drop_column("messages", "voice_size_bytes")
    op.drop_column("messages", "voice_duration_ms")
    op.drop_column("messages", "voice_mime_type")
    op.drop_column("messages", "voice_object_key")
