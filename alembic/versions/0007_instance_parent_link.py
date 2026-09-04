"""Link a phase Instance row to the parent-series slice it overrides.

The merged phase stack used to be spliced by ``instance_number``, but that
column mirrors the DICOM ``InstanceNumber`` tag and is NULL for every file
that omits it (single-image studies routinely do).  With a NULL on both
sides the override never matched and a saved phase rendered the untouched
parent — the filter/segmentation result was written to MinIO and to the DB,
then silently dropped at read time.

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-04
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "instances",
        sa.Column("parent_instance_id", PG_UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_instances_parent_instance_id",
        "instances",
        "instances",
        ["parent_instance_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index(
        "ix_instances_parent_instance_id",
        "instances",
        ["parent_instance_id"],
        postgresql_where=sa.text("parent_instance_id IS NOT NULL"),
    )

    # Backfill what the old scheme could actually match: phase rows whose
    # instance_number is non-NULL and resolves to exactly one parent slice.
    op.execute(
        """
        UPDATE instances AS phase_inst
        SET parent_instance_id = parent_inst.id
        FROM series AS phase_series
        JOIN instances AS parent_inst
          ON parent_inst.series_id = phase_series.parent_series_id
        WHERE phase_inst.series_id = phase_series.id
          AND phase_series.parent_series_id IS NOT NULL
          AND phase_inst.instance_number IS NOT NULL
          AND parent_inst.instance_number = phase_inst.instance_number
        """
    )


def downgrade() -> None:
    op.drop_index("ix_instances_parent_instance_id", table_name="instances")
    op.drop_constraint("fk_instances_parent_instance_id", "instances", type_="foreignkey")
    op.drop_column("instances", "parent_instance_id")
