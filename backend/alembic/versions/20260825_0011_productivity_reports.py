"""add tasks, stable assistant evidence and doctor report snapshots

Revision ID: 20260825_0011
Revises: 20260824_0010
Create Date: 2026-08-25 00:00:00.000000
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260825_0011"
down_revision: str | None = "20260824_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "assistant_messages",
        sa.Column("evidence_snapshot", sa.JSON(), nullable=True),
    )
    op.create_table(
        "health_tasks",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("next_due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("recurrence", sa.String(length=16), nullable=False),
        sa.Column("telegram_enabled", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("source_analysis_result_id", sa.Integer(), nullable=True),
        sa.Column("source_item_id", sa.String(length=80), nullable=True),
        sa.Column("source_snapshot", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["source_analysis_result_id"],
            ["ai_analysis_results.id"],
            name="fk_health_tasks_analysis_result",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_health_tasks"),
    )
    op.create_index(
        "ix_health_tasks_due",
        "health_tasks",
        ["status", "next_due_at"],
        unique=False,
    )
    op.create_table(
        "health_task_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("task_id", sa.String(length=36), nullable=False),
        sa.Column("event_type", sa.String(length=24), nullable=False),
        sa.Column("occurrence_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["health_tasks.id"],
            name="fk_health_task_events_task",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_health_task_events"),
    )
    op.create_index(
        "ix_health_task_events_task",
        "health_task_events",
        ["task_id", "created_at"],
        unique=False,
    )
    op.create_table(
        "health_task_reminder_deliveries",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("task_id", sa.String(length=36), nullable=False),
        sa.Column("occurrence_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("channel", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("outbox_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["outbox_id"],
            ["outbox.id"],
            name="fk_health_task_deliveries_outbox",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["health_tasks.id"],
            name="fk_health_task_deliveries_task",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_health_task_reminder_deliveries"),
        sa.UniqueConstraint("outbox_id", name="uq_health_task_delivery_outbox"),
        sa.UniqueConstraint(
            "task_id",
            "occurrence_at",
            "channel",
            name="uq_health_task_delivery_occurrence",
        ),
    )
    op.create_index(
        "ix_health_task_deliveries_status",
        "health_task_reminder_deliveries",
        ["status", "created_at"],
        unique=False,
    )
    op.create_table(
        "doctor_report_snapshots",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("options", sa.JSON(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("page_count", sa.Integer(), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_doctor_report_snapshots"),
    )
    op.create_index(
        "ix_doctor_report_expiry",
        "doctor_report_snapshots",
        ["expires_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_doctor_report_expiry", table_name="doctor_report_snapshots")
    op.drop_table("doctor_report_snapshots")
    op.drop_index(
        "ix_health_task_deliveries_status",
        table_name="health_task_reminder_deliveries",
    )
    op.drop_table("health_task_reminder_deliveries")
    op.drop_index("ix_health_task_events_task", table_name="health_task_events")
    op.drop_table("health_task_events")
    op.drop_index("ix_health_tasks_due", table_name="health_tasks")
    op.drop_table("health_tasks")
    op.drop_column("assistant_messages", "evidence_snapshot")
