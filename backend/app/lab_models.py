from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, JSON, LargeBinary, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class StoredFile(Base):
    """The durable, database-owned copy of an uploaded original.

    Laboratory files are temporarily dual-written to the legacy root-only file
    directory so the immediately previous release can still be restored.  New
    code always treats this row as the source of truth.
    """

    __tablename__ = "stored_files"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    file_sha256: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    original_filename: Mapped[str] = mapped_column(Text, nullable=False)
    media_type: Mapped[str] = mapped_column(String(80), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class LabDocument(Base):
    __tablename__ = "lab_documents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    stored_file_id: Mapped[str | None] = mapped_column(
        ForeignKey("stored_files.id", ondelete="RESTRICT"), unique=True
    )
    storage_key: Mapped[str] = mapped_column(String(96), unique=True, nullable=False)
    original_filename: Mapped[str] = mapped_column(Text, nullable=False)
    file_sha256: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    media_type: Mapped[str] = mapped_column(String(80), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="queued")
    processing_stage: Mapped[str] = mapped_column(String(32), nullable=False, default="queued")
    progress_percent: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    page_count: Mapped[int | None] = mapped_column(Integer)
    extracted_text: Mapped[str | None] = mapped_column(Text)
    parser_pages: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON)
    error_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    reports: Mapped[list["LabReport"]] = relationship(back_populates="document", cascade="all, delete-orphan")
    results: Mapped[list["LabResult"]] = relationship(back_populates="document", cascade="all, delete-orphan")
    chunks: Mapped[list["LabTextChunk"]] = relationship(back_populates="document", cascade="all, delete-orphan")
    stored_file: Mapped[StoredFile | None] = relationship()


class LabProcessingJob(Base):
    __tablename__ = "lab_processing_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("lab_documents.id", ondelete="CASCADE"), unique=True, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class LabReport(Base):
    __tablename__ = "lab_reports"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("lab_documents.id", ondelete="CASCADE"), nullable=False)
    observed_on: Mapped[date | None] = mapped_column(Date)
    laboratory: Mapped[str | None] = mapped_column(Text)
    specimen: Mapped[str | None] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    document: Mapped[LabDocument] = relationship(back_populates="reports")


class LabAnalyte(Base):
    __tablename__ = "lab_analytes"

    id: Mapped[str] = mapped_column(String(120), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(240), nullable=False)
    aliases: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class LabAnalyteGuide(Base):
    __tablename__ = "lab_analyte_guides"

    analyte_id: Mapped[str] = mapped_column(
        ForeignKey("lab_analytes.id", ondelete="CASCADE"), primary_key=True
    )
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    why_tested: Mapped[str] = mapped_column(Text, nullable=False)
    low_meaning: Mapped[str] = mapped_column(Text, nullable=False)
    high_meaning: Mapped[str] = mapped_column(Text, nullable=False)
    contract_version: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str] = mapped_column(String(80), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class LabAnalyteGuideJob(Base):
    __tablename__ = "lab_analyte_guide_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    analyte_id: Mapped[str] = mapped_column(
        ForeignKey("lab_analytes.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(64))
    contract_version: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class LabResult(Base):
    __tablename__ = "lab_results"
    __table_args__ = (
        UniqueConstraint("document_id", "source_index", name="uq_lab_result_document_source"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("lab_documents.id", ondelete="CASCADE"), nullable=False)
    report_id: Mapped[str | None] = mapped_column(ForeignKey("lab_reports.id", ondelete="SET NULL"))
    analyte_id: Mapped[str | None] = mapped_column(ForeignKey("lab_analytes.id", ondelete="SET NULL"))
    source_index: Mapped[int] = mapped_column(Integer, nullable=False)
    analyte_name: Mapped[str] = mapped_column(String(240), nullable=False)
    value_numeric: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    value_text: Mapped[str | None] = mapped_column(String(240))
    comparator: Mapped[str | None] = mapped_column(String(4))
    unit: Mapped[str | None] = mapped_column(String(80))
    observed_on: Mapped[date | None] = mapped_column(Date)
    specimen: Mapped[str | None] = mapped_column(String(120))
    method: Mapped[str | None] = mapped_column(String(240))
    reference_low: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    reference_high: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    reference_text: Mapped[str | None] = mapped_column(String(240))
    reference_source: Mapped[str] = mapped_column(String(24), nullable=False, default="none")
    laboratory_flag: Mapped[str | None] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="indeterminate")
    verification_status: Mapped[str] = mapped_column(String(24), nullable=False, default="unverified")
    source_page: Mapped[int | None] = mapped_column(Integer)
    deleted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    document: Mapped[LabDocument] = relationship(back_populates="results")
    analyte: Mapped[LabAnalyte | None] = relationship()


class LabExtraction(Base):
    __tablename__ = "lab_extractions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("lab_documents.id", ondelete="CASCADE"), nullable=False)
    contract_version: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str] = mapped_column(String(80), nullable=False)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    raw_result: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class LabResultEdit(Base):
    __tablename__ = "lab_result_edits"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    result_id: Mapped[str] = mapped_column(ForeignKey("lab_results.id", ondelete="CASCADE"), nullable=False)
    before: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    after: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class LabTextChunk(Base):
    __tablename__ = "lab_text_chunks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("lab_documents.id", ondelete="CASCADE"), nullable=False)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    page_from: Mapped[int | None] = mapped_column(Integer)
    page_to: Mapped[int | None] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text, nullable=False)

    document: Mapped[LabDocument] = relationship(back_populates="chunks")


class LabReferenceRange(Base):
    __tablename__ = "lab_reference_ranges"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    catalog_version: Mapped[str] = mapped_column(String(40), nullable=False)
    analyte_id: Mapped[str] = mapped_column(ForeignKey("lab_analytes.id", ondelete="CASCADE"), nullable=False)
    specimen: Mapped[str] = mapped_column(String(120), nullable=False)
    unit: Mapped[str] = mapped_column(String(80), nullable=False)
    reference_sex: Mapped[str] = mapped_column(String(16), nullable=False, default="any")
    min_age_years: Mapped[int | None] = mapped_column(Integer)
    max_age_years: Mapped[int | None] = mapped_column(Integer)
    low: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    high: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    reference_text: Mapped[str | None] = mapped_column(String(240))
    source: Mapped[str] = mapped_column(Text, nullable=False)
    reviewed_on: Mapped[date] = mapped_column(Date, nullable=False)


class AssistantMessage(Base):
    __tablename__ = "assistant_messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    client_request_id: Mapped[str | None] = mapped_column(String(80), unique=True)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    draft_segments: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    evidence_keys: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    evidence_snapshot: Mapped[dict[str, dict[str, Any]] | None] = mapped_column(JSON)
    error_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AssistantJob(Base):
    __tablename__ = "assistant_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_message_id: Mapped[str] = mapped_column(ForeignKey("assistant_messages.id", ondelete="CASCADE"), unique=True, nullable=False)
    assistant_message_id: Mapped[str] = mapped_column(ForeignKey("assistant_messages.id", ondelete="CASCADE"), unique=True, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AssistantSummary(Base):
    __tablename__ = "assistant_summary"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    summarized_through: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class StudyDocument(Base):
    __tablename__ = "study_documents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    storage_key: Mapped[str] = mapped_column(String(96), unique=True, nullable=False)
    stored_file_id: Mapped[str] = mapped_column(
        ForeignKey("stored_files.id", ondelete="RESTRICT"), unique=True, nullable=False
    )
    original_filename: Mapped[str] = mapped_column(Text, nullable=False)
    file_sha256: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    media_type: Mapped[str] = mapped_column(String(80), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    modality: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str | None] = mapped_column(String(240))
    observed_on: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="queued")
    processing_stage: Mapped[str] = mapped_column(String(32), nullable=False, default="queued")
    progress_percent: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    page_count: Mapped[int | None] = mapped_column(Integer)
    extracted_text: Mapped[str | None] = mapped_column(Text)
    findings: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    conclusion: Mapped[str | None] = mapped_column(Text)
    error_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    stored_file: Mapped[StoredFile] = relationship()


class StudyProcessingJob(Base):
    __tablename__ = "study_processing_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    document_id: Mapped[str] = mapped_column(
        ForeignKey("study_documents.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
