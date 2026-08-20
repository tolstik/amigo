"""Database originals, visible processing stages, and study reports.

Revision ID: 20260820_0006
Revises: 20260820_0005
"""
from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260820_0006"
down_revision: str | None = "20260820_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _notify_trigger(table: str, trigger: str) -> None:
    op.execute(
        f"CREATE TRIGGER {trigger} AFTER INSERT OR UPDATE OF status ON {table} "
        "FOR EACH ROW WHEN (NEW.status = 'pending') "
        "EXECUTE FUNCTION amigo_notify_background_work()"
    )


def _queue_trigger(table: str, trigger: str) -> None:
    op.execute(
        f"CREATE TRIGGER {trigger} AFTER INSERT OR UPDATE ON {table} "
        "FOR EACH ROW EXECUTE FUNCTION amigo_notify_queue_event()"
    )


def upgrade() -> None:
    op.create_table(
        "stored_files",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("file_sha256", sa.String(64), nullable=False, unique=True),
        sa.Column("original_filename", sa.Text(), nullable=False),
        sa.Column("media_type", sa.String(80), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("content", sa.LargeBinary(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.add_column("lab_documents", sa.Column("stored_file_id", sa.String(36)))
    op.add_column(
        "lab_documents",
        sa.Column("processing_stage", sa.String(32), nullable=False, server_default="queued"),
    )
    op.add_column(
        "lab_documents",
        sa.Column("progress_percent", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_foreign_key(
        "fk_lab_documents_stored_file",
        "lab_documents",
        "stored_files",
        ["stored_file_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_unique_constraint(
        "uq_lab_documents_stored_file", "lab_documents", ["stored_file_id"]
    )
    op.create_table(
        "study_documents",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("storage_key", sa.String(96), nullable=False, unique=True),
        sa.Column("stored_file_id", sa.String(36), sa.ForeignKey("stored_files.id", ondelete="RESTRICT"), nullable=False, unique=True),
        sa.Column("original_filename", sa.Text(), nullable=False),
        sa.Column("file_sha256", sa.String(64), nullable=False, unique=True),
        sa.Column("media_type", sa.String(80), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("modality", sa.String(32), nullable=False),
        sa.Column("title", sa.String(240)),
        sa.Column("observed_on", sa.Date()),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("processing_stage", sa.String(32), nullable=False),
        sa.Column("progress_percent", sa.Integer(), nullable=False),
        sa.Column("verified", sa.Boolean(), nullable=False),
        sa.Column("page_count", sa.Integer()),
        sa.Column("extracted_text", sa.Text()),
        sa.Column("findings", sa.JSON(), nullable=False),
        sa.Column("conclusion", sa.Text()),
        sa.Column("error_code", sa.String(64)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_study_documents_created", "study_documents", ["created_at"])
    op.create_table(
        "study_processing_jobs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("document_id", sa.String(36), sa.ForeignKey("study_documents.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("error_code", sa.String(64)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_study_jobs_claim", "study_processing_jobs", ["status", "available_at", "id"])

    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            "CREATE FUNCTION amigo_notify_background_work() RETURNS trigger "
            "LANGUAGE plpgsql AS $$ BEGIN PERFORM pg_notify('amigo_ai_work', TG_TABLE_NAME); RETURN NEW; END $$"
        )
        op.execute(
            "CREATE FUNCTION amigo_notify_queue_event() RETURNS trigger "
            "LANGUAGE plpgsql AS $$ BEGIN PERFORM pg_notify('amigo_queue_events', TG_TABLE_NAME); RETURN NEW; END $$"
        )
        _notify_trigger("lab_processing_jobs", "trg_lab_jobs_notify")
        _notify_trigger("assistant_jobs", "trg_assistant_jobs_notify")
        _notify_trigger("ai_analysis_jobs", "trg_ai_jobs_notify")
        _notify_trigger("study_processing_jobs", "trg_study_jobs_notify")
        _queue_trigger("lab_documents", "trg_lab_documents_queue_notify")
        _queue_trigger("study_documents", "trg_study_documents_queue_notify")


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        for trigger, table in (
            ("trg_study_documents_queue_notify", "study_documents"),
            ("trg_lab_documents_queue_notify", "lab_documents"),
        ):
            op.execute(f"DROP TRIGGER IF EXISTS {trigger} ON {table}")
        for trigger, table in (
            ("trg_study_jobs_notify", "study_processing_jobs"),
            ("trg_ai_jobs_notify", "ai_analysis_jobs"),
            ("trg_assistant_jobs_notify", "assistant_jobs"),
            ("trg_lab_jobs_notify", "lab_processing_jobs"),
        ):
            op.execute(f"DROP TRIGGER IF EXISTS {trigger} ON {table}")
        op.execute("DROP FUNCTION IF EXISTS amigo_notify_queue_event()")
        op.execute("DROP FUNCTION IF EXISTS amigo_notify_background_work()")
    op.drop_index("ix_study_jobs_claim", table_name="study_processing_jobs")
    op.drop_table("study_processing_jobs")
    op.drop_index("ix_study_documents_created", table_name="study_documents")
    op.drop_table("study_documents")
    op.drop_constraint("uq_lab_documents_stored_file", "lab_documents", type_="unique")
    op.drop_constraint("fk_lab_documents_stored_file", "lab_documents", type_="foreignkey")
    op.drop_column("lab_documents", "progress_percent")
    op.drop_column("lab_documents", "processing_stage")
    op.drop_column("lab_documents", "stored_file_id")
    op.drop_table("stored_files")
