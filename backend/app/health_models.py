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
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base
from .models import utcnow


class HealthConnectDevice(Base):
    """An Android installation authorised to upload Health Connect data."""

    __tablename__ = "health_connect_devices"
    __table_args__ = (
        Index("ix_hc_devices_status_created", "status", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    label: Mapped[str] = mapped_column(String(80), nullable=False)
    public_key_pem: Mapped[str] = mapped_column(Text, nullable=False)
    public_key_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    pairing_code_hash: Mapped[str | None] = mapped_column(String(64), unique=True)
    pairing_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    data_origin: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    data_as_of: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Only short, sanitised machine codes belong here; never persist exception text.
    last_error: Mapped[str | None] = mapped_column(String(64))


class HealthConnectBatch(Base):
    """Replay ledger and snapshot-page metadata; the raw request is never stored."""

    __tablename__ = "health_connect_batches"
    __table_args__ = (
        UniqueConstraint("device_id", "batch_id", name="uq_hc_batch_device_batch"),
        UniqueConstraint("device_id", "nonce", name="uq_hc_batch_device_nonce"),
        Index("ix_hc_batch_snapshot", "device_id", "snapshot_id", "page_index"),
        Index("ix_hc_batch_accepted", "accepted_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    device_id: Mapped[str] = mapped_column(
        ForeignKey("health_connect_devices.id", ondelete="CASCADE"), nullable=False
    )
    batch_id: Mapped[str] = mapped_column(String(128), nullable=False)
    nonce: Mapped[str] = mapped_column(String(128), nullable=False)
    client_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    body_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    record_type: Mapped[str] = mapped_column(String(40), nullable=False)
    data_origin: Mapped[str] = mapped_column(String(255), nullable=False)
    data_as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    snapshot_id: Mapped[str | None] = mapped_column(String(128))
    range_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    range_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    page_index: Mapped[int | None] = mapped_column(Integer)
    final_page: Mapped[bool | None] = mapped_column(Boolean)
    record_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    record_count: Mapped[int] = mapped_column(Integer, nullable=False)
    upserted_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    deleted_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    reconciled_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    accepted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class HealthConnectRecord(Base):
    """Normalised allowlisted Health Connect record.

    ``metrics`` contains only server-normalised fields defined in
    :mod:`app.health_ingest`; it is not a copy of the Android or provider payload.
    Tombstones are retained so a deletion cannot silently disappear from the
    synchronisation audit trail.
    """

    __tablename__ = "health_connect_records"
    __table_args__ = (
        UniqueConstraint(
            "device_id", "external_record_id", name="uq_hc_record_device_external"
        ),
        Index("ix_hc_record_type_start", "record_type", "start_time"),
        Index("ix_hc_record_device_type_deleted", "device_id", "record_type", "is_deleted"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    device_id: Mapped[str] = mapped_column(
        ForeignKey("health_connect_devices.id", ondelete="CASCADE"), nullable=False
    )
    external_record_id: Mapped[str] = mapped_column(String(255), nullable=False)
    record_type: Mapped[str] = mapped_column(String(40), nullable=False)
    data_origin: Mapped[str] = mapped_column(String(255), nullable=False)
    start_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    end_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    start_zone_offset_seconds: Mapped[int | None] = mapped_column(Integer)
    end_zone_offset_seconds: Mapped[int | None] = mapped_column(Integer)
    primary_value: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    primary_unit: Mapped[str | None] = mapped_column(String(32))
    subtype: Mapped[str | None] = mapped_column(String(64))
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    is_deleted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    source_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_batch_id: Mapped[int | None] = mapped_column(
        ForeignKey("health_connect_batches.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )
