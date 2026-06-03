"""Scope DICOM uniqueness constraints per-owner instead of globally.

Previously study_instance_uid, series_instance_uid, and sop_instance_uid
were globally unique across all users.  This prevented two different owners
from holding the same study/series/instance UIDs, which is a valid scenario
(e.g. two doctors uploading the same anonymised dataset).

New boundaries:
  studies   — UNIQUE(study_instance_uid, owner_id)
  series    — UNIQUE(series_instance_uid, study_id)   study_id implies owner
  instances — UNIQUE(sop_instance_uid, series_id)     series_id implies owner

Revision ID: 0006
Revises: 0005
Create Date: 2026-06-03
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # studies — drop global, add per-owner
    op.drop_constraint("studies_study_instance_uid_key", "studies", type_="unique")
    op.create_unique_constraint(
        "uq_studies_uid_owner", "studies", ["study_instance_uid", "owner_id"]
    )

    # series — drop global, add per-study (study_id implies owner)
    op.drop_constraint("series_series_instance_uid_key", "series", type_="unique")
    op.create_unique_constraint(
        "uq_series_uid_study", "series", ["series_instance_uid", "study_id"]
    )

    # instances — drop global, add per-series (series_id implies owner)
    op.drop_constraint("instances_sop_instance_uid_key", "instances", type_="unique")
    op.create_unique_constraint(
        "uq_instances_uid_series", "instances", ["sop_instance_uid", "series_id"]
    )


def downgrade() -> None:
    op.drop_constraint("uq_instances_uid_series", "instances", type_="unique")
    op.create_unique_constraint(
        "instances_sop_instance_uid_key", "instances", ["sop_instance_uid"]
    )

    op.drop_constraint("uq_series_uid_study", "series", type_="unique")
    op.create_unique_constraint(
        "series_series_instance_uid_key", "series", ["series_instance_uid"]
    )

    op.drop_constraint("uq_studies_uid_owner", "studies", type_="unique")
    op.create_unique_constraint(
        "studies_study_instance_uid_key", "studies", ["study_instance_uid"]
    )
