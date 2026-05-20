"""Fix patient uniqueness — scope patient_id to (patient_id, created_by).

The original schema had a unique constraint on patient_id alone, which:
  1. Prevented two different users from uploading a study with the same
     patient_id (e.g. anonymised files where patient_id = '0').
  2. Caused UniqueViolation crashes when multiple files from the same folder
     were ingested concurrently (every task raced to INSERT the same patient).

This migration replaces that single-column constraint with a composite one on
(patient_id, created_by), which is the semantically correct uniqueness boundary.

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-19
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Drop the old single-column unique constraint (Postgres auto-name).
    op.drop_constraint("patients_patient_id_key", "patients", type_="unique")

    # Add composite unique constraint scoped to each user's patient space.
    op.create_unique_constraint(
        "uq_patients_patient_id_created_by",
        "patients",
        ["patient_id", "created_by"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_patients_patient_id_created_by", "patients", type_="unique")
    op.create_unique_constraint("patients_patient_id_key", "patients", ["patient_id"])
