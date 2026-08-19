"""Health Connect devices, replay ledger, and normalised records.

Revision ID: 20260819_0003
Revises: 20260819_0002
"""
from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260819_0003"
down_revision: str | None = "20260819_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "health_connect_devices",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("label", sa.String(80), nullable=False),
        sa.Column("public_key_pem", sa.Text(), nullable=False),
        sa.Column("public_key_fingerprint", sa.String(64), nullable=False, unique=True),
        sa.Column("pairing_code_hash", sa.String(64), unique=True),
        sa.Column("pairing_expires_at", sa.DateTime(timezone=True)),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("data_origin", sa.String(255)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("last_sync_at", sa.DateTime(timezone=True)),
        sa.Column("data_as_of", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.String(64)),
    )
    op.create_index(
        "ix_hc_devices_status_created", "health_connect_devices", ["status", "created_at"]
    )
    op.create_table(
        "health_connect_batches",
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
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("mode", sa.String(16), nullable=False),
        sa.Column("record_type", sa.String(40), nullable=False),
        sa.Column("data_origin", sa.String(255), nullable=False),
        sa.Column("data_as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("snapshot_id", sa.String(128)),
        sa.Column("range_start", sa.DateTime(timezone=True)),
        sa.Column("range_end", sa.DateTime(timezone=True)),
        sa.Column("page_index", sa.Integer()),
        sa.Column("final_page", sa.Boolean()),
        sa.Column("record_ids", sa.JSON(), nullable=False),
        sa.Column("record_count", sa.Integer(), nullable=False),
        sa.Column("upserted_count", sa.Integer(), nullable=False),
        sa.Column("deleted_count", sa.Integer(), nullable=False),
        sa.Column("reconciled_count", sa.Integer(), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("device_id", "batch_id", name="uq_hc_batch_device_batch"),
        sa.UniqueConstraint("device_id", "nonce", name="uq_hc_batch_device_nonce"),
    )
    op.create_index(
        "ix_hc_batch_snapshot",
        "health_connect_batches",
        ["device_id", "snapshot_id", "page_index"],
    )
    op.create_index("ix_hc_batch_accepted", "health_connect_batches", ["accepted_at"])
    op.create_table(
        "health_connect_records",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "device_id",
            sa.String(36),
            sa.ForeignKey("health_connect_devices.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("external_record_id", sa.String(255), nullable=False),
        sa.Column("record_type", sa.String(40), nullable=False),
        sa.Column("data_origin", sa.String(255), nullable=False),
        sa.Column("start_time", sa.DateTime(timezone=True)),
        sa.Column("end_time", sa.DateTime(timezone=True)),
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
            sa.ForeignKey("health_connect_batches.id", ondelete="SET NULL"),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "device_id", "external_record_id", name="uq_hc_record_device_external"
        ),
    )
    op.create_index(
        "ix_hc_record_type_start", "health_connect_records", ["record_type", "start_time"]
    )
    op.create_index(
        "ix_hc_record_device_type_deleted",
        "health_connect_records",
        ["device_id", "record_type", "is_deleted"],
    )


def downgrade() -> None:
    op.drop_index("ix_hc_record_device_type_deleted", table_name="health_connect_records")
    op.drop_index("ix_hc_record_type_start", table_name="health_connect_records")
    op.drop_table("health_connect_records")
    op.drop_index("ix_hc_batch_accepted", table_name="health_connect_batches")
    op.drop_index("ix_hc_batch_snapshot", table_name="health_connect_batches")
    op.drop_table("health_connect_batches")
    op.drop_index("ix_hc_devices_status_created", table_name="health_connect_devices")
    op.drop_table("health_connect_devices")
