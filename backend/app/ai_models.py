from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Integer, JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AiAnalysisJob(Base):
    """A deduplicated request for an asynchronous AI narrative."""

    __tablename__ = "ai_analysis_jobs"
    __table_args__ = (
        UniqueConstraint("request_key", name="uq_ai_analysis_job_request_key"),
        Index("ix_ai_analysis_jobs_pending", "status", "available_at"),
        Index("ix_ai_analysis_jobs_source", "source_through", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    request_key: Mapped[str] = mapped_column(String(64), nullable=False)
    snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    source_through: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str] = mapped_column(String(64), nullable=False)
    trigger: Mapped[str] = mapped_column(String(24), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AiAnalysisResult(Base):
    """A validated result. Raw Codex output and prompts are never persisted."""

    __tablename__ = "ai_analysis_results"
    __table_args__ = (
        UniqueConstraint("job_id", name="uq_ai_analysis_result_job"),
        Index("ix_ai_analysis_results_latest", "source_through", "generated_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(
        ForeignKey("ai_analysis_jobs.id", ondelete="CASCADE"), nullable=False
    )
    snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str] = mapped_column(String(64), nullable=False)
    analysis: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    source_through: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    # NULL means this result still matches the latest known snapshot. The
    # first newer snapshot sets a finite cutoff without extending it on every
    # subsequent upload.
    fresh_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
