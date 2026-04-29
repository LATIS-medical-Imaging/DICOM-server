"""Initial schema — all 10 tables.

Revision ID: 0001
Revises:
Create Date: 2026-04-26
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # users
    # ------------------------------------------------------------------
    op.create_table(
        "users",
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
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("email_verified", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("first_name", sa.String(100), nullable=False),
        sa.Column("last_name", sa.String(100), nullable=False),
        sa.Column("title", sa.String(20), nullable=True),
        sa.Column("specialty", sa.String(100), nullable=True),
        sa.Column("license_number", sa.String(50), nullable=True),
        sa.Column("institution", sa.String(200), nullable=True),
        sa.Column("phone", sa.String(20), nullable=True),
        sa.Column("avatar_url", sa.String(500), nullable=True),
        sa.Column("role", sa.String(20), nullable=False, server_default="doctor"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("role IN ('admin', 'doctor')", name="ck_users_role"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email"),
    )
    op.create_index("ix_users_email", "users", ["email"])
    op.create_index("ix_users_deleted_at", "users", ["deleted_at"])

    # ------------------------------------------------------------------
    # user_sessions
    # ------------------------------------------------------------------
    op.create_table(
        "user_sessions",
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
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("refresh_token_hash", sa.String(255), nullable=False),
        sa.Column("token_family", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("ip_address", postgresql.INET(), nullable=True),
        sa.Column("user_agent", sa.String(500), nullable=True),
        sa.Column("device_info", sa.String(200), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column(
            "last_active_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("refresh_token_hash"),
    )
    op.create_index("ix_user_sessions_user_id", "user_sessions", ["user_id"])
    op.create_index("ix_user_sessions_token_family", "user_sessions", ["token_family"])

    # ------------------------------------------------------------------
    # patients
    # ------------------------------------------------------------------
    op.create_table(
        "patients",
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
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("patient_name", sa.String(300), nullable=False),
        sa.Column("patient_id", sa.String(64), nullable=False),
        sa.Column("birth_date", sa.Date(), nullable=False),
        sa.Column("sex", sa.String(1), nullable=False),
        sa.CheckConstraint("sex IN ('M', 'F', 'O')", name="ck_patients_sex"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("patient_id"),
    )
    op.create_index("ix_patients_patient_id", "patients", ["patient_id"])
    op.create_index("ix_patients_created_by", "patients", ["created_by"])

    # ------------------------------------------------------------------
    # studies
    # ------------------------------------------------------------------
    op.create_table(
        "studies",
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
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("patient_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("study_instance_uid", sa.String(128), nullable=False),
        sa.Column("accession_number", sa.String(64), nullable=True),
        sa.Column("study_date", sa.Date(), nullable=True),
        sa.Column("study_time", sa.Time(), nullable=True),
        sa.Column("study_description", sa.String(500), nullable=True),
        sa.Column("modality", sa.String(16), nullable=True),
        sa.Column("referring_physician", sa.String(200), nullable=True),
        sa.Column("institution_name", sa.String(200), nullable=True),
        sa.Column("total_series_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_instance_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_size_bytes", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("storage_path", sa.String(500), nullable=False, server_default=""),
        sa.Column("status", sa.String(20), nullable=False, server_default="processing"),
        sa.CheckConstraint(
            "status IN ('processing', 'ready', 'error', 'archived')",
            name="ck_studies_status",
        ),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["patient_id"], ["patients.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("study_instance_uid"),
    )
    op.create_index("ix_studies_owner_id", "studies", ["owner_id"])
    op.create_index("ix_studies_patient_id", "studies", ["patient_id"])
    op.create_index("ix_studies_deleted_at", "studies", ["deleted_at"])

    # ------------------------------------------------------------------
    # series
    # ------------------------------------------------------------------
    op.create_table(
        "series",
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
        sa.Column("study_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("series_instance_uid", sa.String(128), nullable=False),
        sa.Column("series_number", sa.Integer(), nullable=True),
        sa.Column("modality", sa.String(16), nullable=False),
        sa.Column("series_description", sa.String(500), nullable=True),
        sa.Column("body_part_examined", sa.String(64), nullable=True),
        sa.Column("patient_position", sa.String(16), nullable=True),
        sa.Column("protocol_name", sa.String(200), nullable=True),
        sa.Column("slice_thickness", sa.Numeric(10, 4), nullable=True),
        sa.Column("spacing_between_slices", sa.Numeric(10, 4), nullable=True),
        sa.Column("pixel_spacing", sa.String(64), nullable=True),
        sa.Column("instance_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("storage_path", sa.String(500), nullable=False, server_default=""),
        sa.ForeignKeyConstraint(["study_id"], ["studies.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("series_instance_uid"),
    )
    op.create_index("ix_series_study_id", "series", ["study_id"])

    # ------------------------------------------------------------------
    # instances
    # ------------------------------------------------------------------
    op.create_table(
        "instances",
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
        sa.Column("series_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sop_instance_uid", sa.String(128), nullable=False),
        sa.Column("sop_class_uid", sa.String(128), nullable=True),
        sa.Column("instance_number", sa.Integer(), nullable=True),
        sa.Column("rows", sa.Integer(), nullable=True),
        sa.Column("columns", sa.Integer(), nullable=True),
        sa.Column("bits_allocated", sa.SmallInteger(), nullable=True),
        sa.Column("bits_stored", sa.SmallInteger(), nullable=True),
        sa.Column("pixel_representation", sa.SmallInteger(), nullable=True),
        sa.Column("number_of_frames", sa.Integer(), nullable=True, server_default="1"),
        sa.Column("window_center", sa.Numeric(12, 4), nullable=True),
        sa.Column("window_width", sa.Numeric(12, 4), nullable=True),
        sa.Column("rescale_intercept", sa.Numeric(12, 4), nullable=True),
        sa.Column("rescale_slope", sa.Numeric(12, 4), nullable=True),
        sa.Column("image_position_patient", sa.String(200), nullable=True),
        sa.Column("image_orientation_patient", sa.String(200), nullable=True),
        sa.Column("transfer_syntax_uid", sa.String(128), nullable=True),
        sa.Column("file_path", sa.String(500), nullable=False),
        sa.Column("file_size_bytes", sa.BigInteger(), nullable=False),
        sa.Column(
            "content_type",
            sa.String(50),
            nullable=False,
            server_default="application/dicom",
        ),
        sa.Column("checksum_sha256", sa.String(64), nullable=True),
        sa.ForeignKeyConstraint(["series_id"], ["series.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("sop_instance_uid"),
    )
    op.create_index("ix_instances_series_id", "instances", ["series_id"])

    # ------------------------------------------------------------------
    # annotations
    # ------------------------------------------------------------------
    op.create_table(
        "annotations",
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
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("instance_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("cornerstone_uid", sa.String(128), nullable=True),
        sa.Column("tool_type", sa.String(50), nullable=False),
        sa.Column(
            "annotation_data",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "viewport_state",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("measurement_value", sa.Numeric(15, 6), nullable=True),
        sa.Column("measurement_unit", sa.String(20), nullable=True),
        sa.Column("measurement_area", sa.Numeric(15, 6), nullable=True),
        sa.Column("measurement_mean", sa.Numeric(15, 6), nullable=True),
        sa.Column("measurement_stddev", sa.Numeric(15, 6), nullable=True),
        sa.Column("label", sa.String(500), nullable=True),
        sa.Column("is_visible", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("is_locked", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("color", sa.String(7), nullable=True),
        sa.ForeignKeyConstraint(["instance_id"], ["instances.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_annotations_instance_id", "annotations", ["instance_id"])
    op.create_index("ix_annotations_user_id", "annotations", ["user_id"])
    op.create_index("ix_annotations_deleted_at", "annotations", ["deleted_at"])

    # ------------------------------------------------------------------
    # shares
    # ------------------------------------------------------------------
    op.create_table(
        "shares",
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
        sa.Column("grantor_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("grantee_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("study_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("series_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("instance_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("permission", sa.String(20), nullable=False, server_default="view"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("message", sa.String(500), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "(study_id IS NOT NULL)::int + (series_id IS NOT NULL)::int"
            " + (instance_id IS NOT NULL)::int = 1",
            name="exactly_one_resource",
        ),
        sa.CheckConstraint(
            "permission IN ('view', 'annotate', 'manage')",
            name="ck_shares_permission",
        ),
        sa.CheckConstraint(
            "status IN ('active', 'revoked', 'expired')",
            name="ck_shares_status",
        ),
        sa.ForeignKeyConstraint(["grantor_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["grantee_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["study_id"], ["studies.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["series_id"], ["series.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["instance_id"], ["instances.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_shares_grantee_id", "shares", ["grantee_id"])
    op.create_index("ix_shares_grantor_id", "shares", ["grantor_id"])

    # ------------------------------------------------------------------
    # upload_jobs
    # ------------------------------------------------------------------
    op.create_table(
        "upload_jobs",
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
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("study_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("job_type", sa.String(20), nullable=False, server_default="dicom_upload"),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("total_files", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("processed_files", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed_files", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_bytes", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("processed_bytes", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column(
            "errors",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="[]",
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "job_type IN ('dicom_upload', 'dicom_import', 'bulk_upload')",
            name="ck_upload_jobs_type",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'uploading', 'processing', 'extracting_metadata',"
            " 'storing', 'completed', 'failed', 'cancelled')",
            name="ck_upload_jobs_status",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["study_id"], ["studies.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_upload_jobs_user_id", "upload_jobs", ["user_id"])
    op.create_index("ix_upload_jobs_study_id", "upload_jobs", ["study_id"])

    # ------------------------------------------------------------------
    # audit_logs  (partitioned by month — parent table only)
    # SQLAlchemy cannot express PARTITION BY, so raw DDL is used here.
    # New monthly partitions must be added via subsequent migrations.
    # ------------------------------------------------------------------
    op.execute("""
        CREATE TABLE audit_logs (
            id          BIGSERIAL,
            user_id     UUID        REFERENCES users(id) ON DELETE SET NULL,
            action      VARCHAR(50) NOT NULL,
            resource_type VARCHAR(30),
            resource_id UUID,
            ip_address  INET,
            user_agent  VARCHAR(500),
            session_id  UUID,
            details     JSONB       NOT NULL DEFAULT '{}',
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (id, created_at)
        ) PARTITION BY RANGE (created_at)
    """)

    # Seed partitions — current quarter
    op.execute("""
        CREATE TABLE audit_logs_2026_04 PARTITION OF audit_logs
            FOR VALUES FROM ('2026-04-01') TO ('2026-05-01')
    """)
    op.execute("""
        CREATE TABLE audit_logs_2026_05 PARTITION OF audit_logs
            FOR VALUES FROM ('2026-05-01') TO ('2026-06-01')
    """)
    op.execute("""
        CREATE TABLE audit_logs_2026_06 PARTITION OF audit_logs
            FOR VALUES FROM ('2026-06-01') TO ('2026-07-01')
    """)

    op.create_index("ix_audit_logs_user_id", "audit_logs", ["user_id"])
    op.create_index("ix_audit_logs_resource_type", "audit_logs", ["resource_type", "resource_id"])


def downgrade() -> None:
    op.drop_index("ix_audit_logs_resource_type", "audit_logs")
    op.drop_index("ix_audit_logs_user_id", "audit_logs")
    op.execute("DROP TABLE IF EXISTS audit_logs CASCADE")
    op.drop_table("upload_jobs")
    op.drop_table("shares")
    op.drop_table("annotations")
    op.drop_table("instances")
    op.drop_table("series")
    op.drop_table("studies")
    op.drop_table("patients")
    op.drop_table("user_sessions")
    op.drop_table("users")
