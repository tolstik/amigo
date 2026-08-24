from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base
from .models import utcnow


class MiFitnessSource(Base):
    """Xiaomi Health Cloud state for one already-paired Android installation.

    The Xiaomi account identifier is irreversibly hashed on Android. Authentication
    material remains on the phone and must never be sent to or stored by Amigo.
    """

    __tablename__ = "mi_fitness_sources"
    __table_args__ = (Index("ix_mi_sources_status_updated", "status", "updated_at"),)

    device_id: Mapped[str] = mapped_column(
        ForeignKey("health_connect_devices.id", ondelete="CASCADE"), primary_key=True
    )
    account_fingerprint: Mapped[str | None] = mapped_column(String(64))
    region: Mapped[str | None] = mapped_column(String(8))
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="disabled")
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_status_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    data_as_of: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(String(64))
    auth_episode: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )


class MiFitnessBatch(Base):
    """Signed-request replay ledger and cloud snapshot page metadata."""

    __tablename__ = "mi_fitness_batches"
    __table_args__ = (
        UniqueConstraint("device_id", "batch_id", name="uq_mi_batch_device_batch"),
        UniqueConstraint("device_id", "nonce", name="uq_mi_batch_device_nonce"),
        Index("ix_mi_batch_snapshot", "device_id", "snapshot_id", "page_index"),
        Index("ix_mi_batch_accepted", "accepted_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    device_id: Mapped[str] = mapped_column(
        ForeignKey("health_connect_devices.id", ondelete="CASCADE"), nullable=False
    )
    batch_id: Mapped[str] = mapped_column(String(128), nullable=False)
    nonce: Mapped[str] = mapped_column(String(128), nullable=False)
    client_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    body_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    account_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    record_type: Mapped[str] = mapped_column(String(40), nullable=False)
    data_as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_data_as_of: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    snapshot_id: Mapped[str] = mapped_column(String(128), nullable=False)
    range_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    range_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    page_index: Mapped[int] = mapped_column(Integer, nullable=False)
    final_page: Mapped[bool] = mapped_column(Boolean, nullable=False)
    record_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    record_count: Mapped[int] = mapped_column(Integer, nullable=False)
    changed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    reconciled_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    accepted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class MiFitnessRecord(Base):
    """Allowlisted, normalised Xiaomi Cloud record; never a provider payload."""

    __tablename__ = "mi_fitness_records"
    __table_args__ = (
        UniqueConstraint(
            "device_id",
            "snapshot_id",
            "record_type",
            "external_record_id",
            name="uq_mi_record_snapshot_type_external",
        ),
        Index("ix_mi_record_type_start", "record_type", "start_time"),
        Index("ix_mi_record_device_type_deleted", "device_id", "record_type", "is_deleted"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    device_id: Mapped[str] = mapped_column(
        ForeignKey("health_connect_devices.id", ondelete="CASCADE"), nullable=False
    )
    external_record_id: Mapped[str] = mapped_column(String(255), nullable=False)
    snapshot_id: Mapped[str] = mapped_column(String(128), nullable=False)
    record_type: Mapped[str] = mapped_column(String(40), nullable=False)
    account_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    start_zone_offset_seconds: Mapped[int | None] = mapped_column(Integer)
    end_zone_offset_seconds: Mapped[int | None] = mapped_column(Integer)
    primary_value: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    primary_unit: Mapped[str | None] = mapped_column(String(32))
    subtype: Mapped[str | None] = mapped_column(String(64))
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    is_deleted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    source_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_batch_id: Mapped[int | None] = mapped_column(
        ForeignKey("mi_fitness_batches.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )


class MiFitnessCoverage(Base):
    """A finalised snapshot interval whose cloud view is safe to publish."""

    __tablename__ = "mi_fitness_coverages"
    __table_args__ = (
        UniqueConstraint("device_id", "snapshot_id", name="uq_mi_coverage_device_snapshot"),
        Index("ix_mi_coverage_type_range", "device_id", "record_type", "range_start", "range_end"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    device_id: Mapped[str] = mapped_column(
        ForeignKey("health_connect_devices.id", ondelete="CASCADE"), nullable=False
    )
    snapshot_id: Mapped[str] = mapped_column(String(128), nullable=False)
    record_type: Mapped[str] = mapped_column(String(40), nullable=False)
    account_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    range_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    range_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_data_as_of: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    confirmed_empty: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    finalised_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class MiFitnessStatusReport(Base):
    """Replay ledger for signed source-state reports; bodies are represented by a hash only."""

    __tablename__ = "mi_fitness_status_reports"
    __table_args__ = (
        UniqueConstraint("device_id", "report_id", name="uq_mi_status_device_report"),
        UniqueConstraint("device_id", "nonce", name="uq_mi_status_device_nonce"),
        Index("ix_mi_status_reported", "reported_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    device_id: Mapped[str] = mapped_column(
        ForeignKey("health_connect_devices.id", ondelete="CASCADE"), nullable=False
    )
    report_id: Mapped[str] = mapped_column(String(128), nullable=False)
    nonce: Mapped[str] = mapped_column(String(128), nullable=False)
    client_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    body_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    reported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
