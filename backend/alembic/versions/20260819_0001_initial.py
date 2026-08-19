"""Initial Amigo v2 schema.

Revision ID: 20260819_0001
Revises:
"""
from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260819_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "plans",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("version", sa.String(64), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("start_weight_kg", sa.Numeric(8, 3), nullable=False),
        sa.Column("monthly_change_kg", sa.Numeric(8, 3), nullable=False),
        sa.Column("target_weight_kg", sa.Numeric(8, 3), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("version"),
    )
    op.create_table(
        "provider_credentials",
        sa.Column("provider", sa.String(32), primary_key=True),
        sa.Column("access_token_encrypted", sa.Text(), nullable=False),
        sa.Column("refresh_token_encrypted", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("account_id", sa.String(128)),
        sa.Column("scopes", sa.Text()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "sync_state",
        sa.Column("provider", sa.String(32), primary_key=True),
        sa.Column("lastupdate", sa.Integer()),
        sa.Column("initial_import_done", sa.Boolean(), nullable=False),
        sa.Column("last_success_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.Text()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "outbox",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("event_key", sa.String(255), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("event_key"),
    )
    op.create_index("ix_outbox_pending", "outbox", ["status", "available_at"])
    op.create_table(
        "job_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("job_name", sa.String(64), nullable=False),
        sa.Column("run_key", sa.String(128)),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("run_key"),
    )
    op.create_index("ix_job_runs_name_started", "job_runs", ["job_name", "started_at"])
    op.create_table(
        "measurement_groups",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("provider_group_id", sa.String(128), nullable=False),
        sa.Column("measured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_timezone", sa.String(64)),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("raw_payload", sa.JSON(), nullable=False),
        sa.Column("provider_updated_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("provider", "provider_group_id", name="uq_group_provider_id"),
    )
    op.create_index("ix_group_measured_at", "measurement_groups", ["measured_at"])
    op.create_table(
        "measurements",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "group_id",
            sa.Integer(),
            sa.ForeignKey("measurement_groups.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(48), nullable=False),
        sa.Column("value", sa.Numeric(14, 6), nullable=False),
        sa.Column("unit", sa.String(24), nullable=False),
        sa.Column("raw_type", sa.Integer()),
        sa.Column("raw_index", sa.Integer(), nullable=False),
        sa.Column("is_outlier", sa.Boolean(), nullable=False),
        sa.UniqueConstraint("group_id", "kind", "raw_index", name="uq_measurement_kind_index"),
    )
    op.create_index("ix_measurement_kind", "measurements", ["kind"])


def downgrade() -> None:
    op.drop_index("ix_measurement_kind", table_name="measurements")
    op.drop_table("measurements")
    op.drop_index("ix_group_measured_at", table_name="measurement_groups")
    op.drop_table("measurement_groups")
    op.drop_index("ix_job_runs_name_started", table_name="job_runs")
    op.drop_table("job_runs")
    op.drop_index("ix_outbox_pending", table_name="outbox")
    op.drop_table("outbox")
    op.drop_table("sync_state")
    op.drop_table("provider_credentials")
    op.drop_table("plans")
