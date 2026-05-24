"""Series Phases — branch saved-modification snapshots off the originals.

Adds two nullable columns to ``series``:

* ``parent_series_id`` — self-FK with ON DELETE CASCADE.  ``NULL`` marks an
  original DICOM-ingested series; non-NULL marks a phase derived from that
  parent.  Deleting the parent series cleans up every phase that hung off it.
* ``owner_id`` — FK to ``users.id`` with ON DELETE SET NULL.  Carries the
  doctor who saved the phase.  Originals leave this ``NULL`` (their visibility
  is scoped through the parent study's ownership / shares model).

Both columns get partial indexes covering only non-NULL values, since
originals — the dominant majority — would otherwise pollute the index.

No data migration is needed: the existing rows (all originals) keep both new
columns at ``NULL``, which is exactly the semantics we want.

Revision ID: 0004
Revises: 0003
Create Date: 2026-05-24
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "series",
        sa.Column(
            "parent_series_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.add_column(
        "series",
        sa.Column(
            "owner_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.create_foreign_key(
        "fk_series_parent_series_id",
        "series",
        "series",
        ["parent_series_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_series_owner_id",
        "series",
        "users",
        ["owner_id"],
        ["id"],
        ondelete="SET NULL",
    )
    # Partial indexes — originals are the bulk of the table and don't need to
    # appear in either index.
    op.create_index(
        "ix_series_parent_series_id",
        "series",
        ["parent_series_id"],
        postgresql_where=sa.text("parent_series_id IS NOT NULL"),
    )
    op.create_index(
        "ix_series_owner_id",
        "series",
        ["owner_id"],
        postgresql_where=sa.text("owner_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_series_owner_id", table_name="series")
    op.drop_index("ix_series_parent_series_id", table_name="series")
    op.drop_constraint("fk_series_owner_id", "series", type_="foreignkey")
    op.drop_constraint("fk_series_parent_series_id", "series", type_="foreignkey")
    op.drop_column("series", "owner_id")
    op.drop_column("series", "parent_series_id")
