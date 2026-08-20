"""Laboratory archive, deterministic results, and assistant chat.

Revision ID: 20260820_0005
Revises: 20260820_0004
"""
from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260820_0005"
down_revision: str | None = "20260820_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "lab_documents",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("storage_key", sa.String(96), nullable=False, unique=True),
        sa.Column("original_filename", sa.Text(), nullable=False),
        sa.Column("file_sha256", sa.String(64), nullable=False, unique=True),
        sa.Column("media_type", sa.String(80), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("verified", sa.Boolean(), nullable=False),
        sa.Column("page_count", sa.Integer()),
        sa.Column("extracted_text", sa.Text()),
        sa.Column("parser_pages", sa.JSON()),
        sa.Column("error_code", sa.String(64)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_lab_documents_status_created", "lab_documents", ["status", "created_at"])
    op.create_table(
        "lab_processing_jobs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("document_id", sa.String(36), sa.ForeignKey("lab_documents.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("error_code", sa.String(64)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_lab_jobs_claim", "lab_processing_jobs", ["status", "available_at", "id"])
    op.create_table(
        "lab_reports",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("document_id", sa.String(36), sa.ForeignKey("lab_documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("observed_on", sa.Date()),
        sa.Column("laboratory", sa.Text()),
        sa.Column("specimen", sa.String(120)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_lab_reports_document", "lab_reports", ["document_id"])
    op.create_table(
        "lab_analytes",
        sa.Column("id", sa.String(120), primary_key=True),
        sa.Column("display_name", sa.String(240), nullable=False),
        sa.Column("aliases", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "lab_results",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("document_id", sa.String(36), sa.ForeignKey("lab_documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("report_id", sa.String(36), sa.ForeignKey("lab_reports.id", ondelete="SET NULL")),
        sa.Column("analyte_id", sa.String(120), sa.ForeignKey("lab_analytes.id", ondelete="SET NULL")),
        sa.Column("source_index", sa.Integer(), nullable=False),
        sa.Column("analyte_name", sa.String(240), nullable=False),
        sa.Column("value_numeric", sa.Numeric(18, 6)),
        sa.Column("value_text", sa.String(240)),
        sa.Column("comparator", sa.String(4)),
        sa.Column("unit", sa.String(80)),
        sa.Column("observed_on", sa.Date()),
        sa.Column("specimen", sa.String(120)),
        sa.Column("method", sa.String(240)),
        sa.Column("reference_low", sa.Numeric(18, 6)),
        sa.Column("reference_high", sa.Numeric(18, 6)),
        sa.Column("reference_text", sa.String(240)),
        sa.Column("reference_source", sa.String(24), nullable=False),
        sa.Column("laboratory_flag", sa.String(80)),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("verification_status", sa.String(24), nullable=False),
        sa.Column("source_page", sa.Integer()),
        sa.Column("deleted", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("document_id", "source_index", name="uq_lab_result_document_source"),
    )
    op.create_index("ix_lab_results_history", "lab_results", ["analyte_id", "observed_on"])
    op.create_index("ix_lab_results_document", "lab_results", ["document_id", "deleted"])
    op.create_table(
        "lab_extractions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("document_id", sa.String(36), sa.ForeignKey("lab_documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("contract_version", sa.String(64), nullable=False),
        sa.Column("model", sa.String(80), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("raw_result", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "lab_result_edits",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("result_id", sa.String(36), sa.ForeignKey("lab_results.id", ondelete="CASCADE"), nullable=False),
        sa.Column("before", sa.JSON(), nullable=False),
        sa.Column("after", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "lab_text_chunks",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("document_id", sa.String(36), sa.ForeignKey("lab_documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("page_from", sa.Integer()),
        sa.Column("page_to", sa.Integer()),
        sa.Column("content", sa.Text(), nullable=False),
    )
    op.create_index("ix_lab_chunks_document", "lab_text_chunks", ["document_id", "chunk_index"])
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            "CREATE INDEX ix_lab_chunks_fts ON lab_text_chunks "
            "USING gin (to_tsvector('russian'::regconfig, content))"
        )
    op.create_table(
        "lab_reference_ranges",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("catalog_version", sa.String(40), nullable=False),
        sa.Column("analyte_id", sa.String(120), sa.ForeignKey("lab_analytes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("specimen", sa.String(120), nullable=False),
        sa.Column("unit", sa.String(80), nullable=False),
        sa.Column("reference_sex", sa.String(16), nullable=False),
        sa.Column("min_age_years", sa.Integer()),
        sa.Column("max_age_years", sa.Integer()),
        sa.Column("low", sa.Numeric(18, 6)),
        sa.Column("high", sa.Numeric(18, 6)),
        sa.Column("reference_text", sa.String(240)),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("reviewed_on", sa.Date(), nullable=False),
    )
    op.create_index("ix_lab_reference_match", "lab_reference_ranges", ["analyte_id", "specimen", "unit", "reference_sex"])
    op.create_table(
        "assistant_messages",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("client_request_id", sa.String(80), unique=True),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("draft_segments", sa.JSON(), nullable=False),
        sa.Column("evidence_keys", sa.JSON(), nullable=False),
        sa.Column("error_code", sa.String(64)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_assistant_messages_created", "assistant_messages", ["created_at", "id"])
    op.create_table(
        "assistant_jobs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_message_id", sa.String(36), sa.ForeignKey("assistant_messages.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("assistant_message_id", sa.String(36), sa.ForeignKey("assistant_messages.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("error_code", sa.String(64)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_assistant_jobs_claim", "assistant_jobs", ["status", "available_at", "id"])
    op.create_table(
        "assistant_summary",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("summarized_through", sa.DateTime(timezone=True)),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("assistant_summary")
    op.drop_index("ix_assistant_jobs_claim", table_name="assistant_jobs")
    op.drop_table("assistant_jobs")
    op.drop_index("ix_assistant_messages_created", table_name="assistant_messages")
    op.drop_table("assistant_messages")
    op.drop_index("ix_lab_reference_match", table_name="lab_reference_ranges")
    op.drop_table("lab_reference_ranges")
    if op.get_bind().dialect.name == "postgresql":
        op.drop_index("ix_lab_chunks_fts", table_name="lab_text_chunks")
    op.drop_index("ix_lab_chunks_document", table_name="lab_text_chunks")
    op.drop_table("lab_text_chunks")
    op.drop_table("lab_result_edits")
    op.drop_table("lab_extractions")
    op.drop_index("ix_lab_results_document", table_name="lab_results")
    op.drop_index("ix_lab_results_history", table_name="lab_results")
    op.drop_table("lab_results")
    op.drop_table("lab_analytes")
    op.drop_index("ix_lab_reports_document", table_name="lab_reports")
    op.drop_table("lab_reports")
    op.drop_index("ix_lab_jobs_claim", table_name="lab_processing_jobs")
    op.drop_table("lab_processing_jobs")
    op.drop_index("ix_lab_documents_status_created", table_name="lab_documents")
    op.drop_table("lab_documents")
