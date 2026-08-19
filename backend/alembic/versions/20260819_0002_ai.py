"""Add asynchronous AI analysis jobs and validated results.

Revision ID: 20260819_0002
Revises: 20260819_0001
"""
from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260819_0002"
down_revision: str | None = "20260819_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ai_analysis_jobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("request_key", sa.String(64), nullable=False),
        sa.Column("snapshot_hash", sa.String(64), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("source_through", sa.DateTime(timezone=True), nullable=False),
        sa.Column("prompt_version", sa.String(64), nullable=False),
        sa.Column("model", sa.String(64), nullable=False),
        sa.Column("trigger", sa.String(24), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("last_error_code", sa.String(64)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("request_key", name="uq_ai_analysis_job_request_key"),
    )
    op.create_index(
        "ix_ai_analysis_jobs_pending",
        "ai_analysis_jobs",
        ["status", "available_at"],
    )
    op.create_index(
        "ix_ai_analysis_jobs_source",
        "ai_analysis_jobs",
        ["source_through", "created_at"],
    )
    op.create_table(
        "ai_analysis_results",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "job_id",
            sa.Integer(),
            sa.ForeignKey("ai_analysis_jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("snapshot_hash", sa.String(64), nullable=False),
        sa.Column("prompt_version", sa.String(64), nullable=False),
        sa.Column("model", sa.String(64), nullable=False),
        sa.Column("analysis", sa.JSON(), nullable=False),
        sa.Column("source_through", sa.DateTime(timezone=True), nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("fresh_until", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("job_id", name="uq_ai_analysis_result_job"),
    )
    op.create_index(
        "ix_ai_analysis_results_latest",
        "ai_analysis_results",
        ["source_through", "generated_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_ai_analysis_results_latest", table_name="ai_analysis_results")
    op.drop_table("ai_analysis_results")
    op.drop_index("ix_ai_analysis_jobs_source", table_name="ai_analysis_jobs")
    op.drop_index("ix_ai_analysis_jobs_pending", table_name="ai_analysis_jobs")
    op.drop_table("ai_analysis_jobs")
