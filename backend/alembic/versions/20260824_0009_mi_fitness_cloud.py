"""Add rollback-safe Xiaomi Health Cloud snapshot storage.

Revision ID: 20260824_0009
Revises: 20260821_0008
"""
from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260824_0009"
down_revision: str | None = "20260821_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "mi_fitness_sources",
        sa.Column(
            "device_id",
            sa.String(36),
            sa.ForeignKey("health_connect_devices.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("account_fingerprint", sa.String(64)),
        sa.Column("region", sa.String(8)),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("activated_at", sa.DateTime(timezone=True)),
        sa.Column("last_status_at", sa.DateTime(timezone=True)),
        sa.Column("last_success_at", sa.DateTime(timezone=True)),
        sa.Column("data_as_of", sa.DateTime(timezone=True)),
        sa.Column("last_error_code", sa.String(64)),
        sa.Column("auth_episode", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_mi_sources_status_updated", "mi_fitness_sources", ["status", "updated_at"]
    )

    op.create_table(
        "mi_fitness_batches",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "device_id",
            sa.String(36),
            sa.ForeignKey("health_connect_devices.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("batch_id", sa.String(128), nullable=False),
        sa.Column("nonce", sa.String(128), nullable=False),
        sa.Column("client_timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("body_sha256", sa.String(64), nullable=False),
        sa.Column("account_fingerprint", sa.String(64), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("record_type", sa.String(40), nullable=False),
        sa.Column("data_as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_data_as_of", sa.DateTime(timezone=True)),
        sa.Column("snapshot_id", sa.String(128), nullable=False),
        sa.Column("range_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("range_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("page_index", sa.Integer(), nullable=False),
        sa.Column("final_page", sa.Boolean(), nullable=False),
        sa.Column("record_ids", sa.JSON(), nullable=False),
        sa.Column("record_count", sa.Integer(), nullable=False),
        sa.Column("changed_count", sa.Integer(), nullable=False),
        sa.Column("reconciled_count", sa.Integer(), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("device_id", "batch_id", name="uq_mi_batch_device_batch"),
        sa.UniqueConstraint("device_id", "nonce", name="uq_mi_batch_device_nonce"),
    )
    op.create_index(
        "ix_mi_batch_snapshot",
        "mi_fitness_batches",
        ["device_id", "snapshot_id", "page_index"],
    )
    op.create_index("ix_mi_batch_accepted", "mi_fitness_batches", ["accepted_at"])

    op.create_table(
        "mi_fitness_records",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "device_id",
            sa.String(36),
            sa.ForeignKey("health_connect_devices.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("external_record_id", sa.String(255), nullable=False),
        sa.Column("snapshot_id", sa.String(128), nullable=False),
        sa.Column("record_type", sa.String(40), nullable=False),
        sa.Column("account_fingerprint", sa.String(64), nullable=False),
        sa.Column("start_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("start_zone_offset_seconds", sa.Integer()),
        sa.Column("end_zone_offset_seconds", sa.Integer()),
        sa.Column("primary_value", sa.Numeric(18, 6)),
        sa.Column("primary_unit", sa.String(32)),
        sa.Column("subtype", sa.String(64)),
        sa.Column("metrics", sa.JSON(), nullable=False),
        sa.Column("is_deleted", sa.Boolean(), nullable=False),
        sa.Column("source_updated_at", sa.DateTime(timezone=True)),
        sa.Column(
            "source_batch_id",
            sa.Integer(),
            sa.ForeignKey("mi_fitness_batches.id", ondelete="SET NULL"),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "device_id",
            "snapshot_id",
            "record_type",
            "external_record_id",
            name="uq_mi_record_snapshot_type_external",
        ),
    )
    op.create_index(
        "ix_mi_record_type_start", "mi_fitness_records", ["record_type", "start_time"]
    )
    op.create_index(
        "ix_mi_record_device_type_deleted",
        "mi_fitness_records",
        ["device_id", "record_type", "is_deleted"],
    )

    op.create_table(
        "mi_fitness_coverages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "device_id",
            sa.String(36),
            sa.ForeignKey("health_connect_devices.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("snapshot_id", sa.String(128), nullable=False),
        sa.Column("record_type", sa.String(40), nullable=False),
        sa.Column("account_fingerprint", sa.String(64), nullable=False),
        sa.Column("range_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("range_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_data_as_of", sa.DateTime(timezone=True)),
        sa.Column("confirmed_empty", sa.Boolean(), nullable=False),
        sa.Column("finalised_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("device_id", "snapshot_id", name="uq_mi_coverage_device_snapshot"),
    )
    op.create_index(
        "ix_mi_coverage_type_range",
        "mi_fitness_coverages",
        ["device_id", "record_type", "range_start", "range_end"],
    )

    op.create_table(
        "mi_fitness_status_reports",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "device_id",
            sa.String(36),
            sa.ForeignKey("health_connect_devices.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("report_id", sa.String(128), nullable=False),
        sa.Column("nonce", sa.String(128), nullable=False),
        sa.Column("client_timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("body_sha256", sa.String(64), nullable=False),
        sa.Column("reported_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("device_id", "report_id", name="uq_mi_status_device_report"),
        sa.UniqueConstraint("device_id", "nonce", name="uq_mi_status_device_nonce"),
    )
    op.create_index(
        "ix_mi_status_reported", "mi_fitness_status_reports", ["reported_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_mi_status_reported", table_name="mi_fitness_status_reports")
    op.drop_table("mi_fitness_status_reports")
    op.drop_index("ix_mi_coverage_type_range", table_name="mi_fitness_coverages")
    op.drop_table("mi_fitness_coverages")
    op.drop_index("ix_mi_record_device_type_deleted", table_name="mi_fitness_records")
    op.drop_index("ix_mi_record_type_start", table_name="mi_fitness_records")
    op.drop_table("mi_fitness_records")
    op.drop_index("ix_mi_batch_accepted", table_name="mi_fitness_batches")
    op.drop_index("ix_mi_batch_snapshot", table_name="mi_fitness_batches")
    op.drop_table("mi_fitness_batches")
    op.drop_index("ix_mi_sources_status_updated", table_name="mi_fitness_sources")
    op.drop_table("mi_fitness_sources")
