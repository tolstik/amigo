from __future__ import annotations

from datetime import datetime, timedelta
import math
import re
from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator, model_validator


HealthRecordType = Literal[
    "steps",
    "distance",
    "active_calories",
    "total_calories",
    "exercise",
    "sleep",
    "heart_rate",
    "resting_heart_rate",
    "hrv_rmssd",
    "oxygen_saturation",
    "vo2_max",
]
BatchMode = Literal["changes", "snapshot"]

ALLOWED_RECORD_TYPES: frozenset[str] = frozenset(
    (
        "steps",
        "distance",
        "active_calories",
        "total_calories",
        "exercise",
        "sleep",
        "heart_rate",
        "resting_heart_rate",
        "hrv_rmssd",
        "oxygen_saturation",
        "vo2_max",
    )
)

_SAFE_ID = re.compile(r"^[A-Za-z0-9._:/+=-]+$")
_DATA_ORIGIN = re.compile(r"^[A-Za-z0-9_.-]+$")


class DeviceRegistrationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    label: str = Field(min_length=1, max_length=80)
    public_key_pem: str = Field(min_length=100, max_length=2048)

    @field_validator("label")
    @classmethod
    def safe_label(cls, value: str) -> str:
        if any(ord(character) < 32 for character in value):
            raise ValueError("label contains control characters")
        return value


class DeviceRegistrationResponse(BaseModel):
    device_id: str
    status: Literal["pending", "approved", "revoked"]
    pairing_code: str | None = None
    pairing_expires_at: datetime | None = None


class DeviceStatusResponse(BaseModel):
    device_id: str
    status: Literal["pending", "approved", "revoked"]
    last_sync_at: datetime | None = None
    data_as_of: datetime | None = None
    last_error: str | None = None
    mi_fitness_enabled: bool = False
    mi_fitness_active: bool = False
    mi_fitness_status: Literal[
        "pending",
        "success",
        "auth_required",
        "rate_limited",
        "network_error",
        "disabled",
    ] = "disabled"
    mi_fitness_last_success_at: datetime | None = None
    mi_fitness_data_as_of: datetime | None = None
    mi_fitness_last_error: str | None = None


class HealthRecordInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, populate_by_name=True)

    record_id: str = Field(min_length=1, max_length=255)
    type: HealthRecordType
    data_origin: str = Field(min_length=1, max_length=255)
    start_time: datetime | None = None
    end_time: datetime | None = None
    updated_at: datetime | None = Field(
        default=None,
        validation_alias=AliasChoices("updated_at", "last_modified_time"),
    )
    deleted: bool = False
    values: dict[str, Any] = Field(default_factory=dict)

    @field_validator("record_id")
    @classmethod
    def safe_record_id(cls, value: str) -> str:
        if not _SAFE_ID.fullmatch(value):
            raise ValueError("record_id contains unsupported characters")
        return value

    @field_validator("data_origin")
    @classmethod
    def safe_data_origin(cls, value: str) -> str:
        if not _DATA_ORIGIN.fullmatch(value):
            raise ValueError("data_origin must be an Android package-style identifier")
        return value

    @field_validator("start_time", "end_time", "updated_at")
    @classmethod
    def aware_timestamp(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("timestamps must contain an offset")
        return value

    @field_validator("values")
    @classmethod
    def primitive_values(cls, value: dict[str, Any]) -> dict[str, Any]:
        if len(value) > 16:
            raise ValueError("too many value fields")

        def validate_item(item: Any, depth: int = 0) -> None:
            if depth > 2:
                raise ValueError("value nesting is too deep")
            if isinstance(item, bool) or item is None:
                raise ValueError("unsupported value type")
            if isinstance(item, (int, float)):
                if isinstance(item, float) and not math.isfinite(item):
                    raise ValueError("numeric values must be finite")
                return
            if isinstance(item, str):
                if len(item) > 128 or any(ord(character) < 32 for character in item):
                    raise ValueError("text value is invalid")
                return
            if isinstance(item, list):
                if len(item) > 5_000:
                    raise ValueError("value list is too long")
                for child in item:
                    validate_item(child, depth + 1)
                return
            if isinstance(item, dict):
                if len(item) > 8:
                    raise ValueError("nested value object is too large")
                for key, child in item.items():
                    if (
                        not isinstance(key, str)
                        or len(key) > 64
                        or not _SAFE_ID.fullmatch(key)
                    ):
                        raise ValueError("unsupported nested value field")
                    validate_item(child, depth + 1)
                return
            raise ValueError("unsupported value type")

        for key, item in value.items():
            if not isinstance(key, str) or len(key) > 64 or not _SAFE_ID.fullmatch(key):
                raise ValueError("unsupported value field")
            validate_item(item)
        return value

    @model_validator(mode="after")
    def valid_interval(self) -> HealthRecordInput:
        if self.deleted:
            if self.values:
                raise ValueError("deleted records cannot contain values")
            return self
        if self.start_time is None:
            raise ValueError("start_time is required for an active record")
        end = self.end_time or self.start_time
        if end < self.start_time:
            raise ValueError("end_time cannot precede start_time")
        if end - self.start_time > timedelta(days=7):
            raise ValueError("record interval is too long")
        return self


class HealthBatchInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_version: Literal[1] = 1
    batch_id: str | None = Field(default=None, min_length=1, max_length=128)
    mode: BatchMode = "changes"
    record_type: HealthRecordType
    data_origin: str = Field(min_length=1, max_length=255)
    data_as_of: datetime
    range_start: datetime | None = None
    range_end: datetime | None = None
    snapshot_id: str | None = Field(default=None, min_length=1, max_length=128)
    page_index: int | None = Field(default=None, ge=0, le=100_000)
    final_page: bool | None = None
    records: list[HealthRecordInput] = Field(default_factory=list, max_length=2_000)

    @field_validator("data_origin")
    @classmethod
    def safe_data_origin(cls, value: str) -> str:
        if not _DATA_ORIGIN.fullmatch(value):
            raise ValueError("data_origin must be an Android package-style identifier")
        return value

    @field_validator("snapshot_id", "batch_id")
    @classmethod
    def safe_snapshot_id(cls, value: str | None) -> str | None:
        if value is not None and not _SAFE_ID.fullmatch(value):
            raise ValueError("snapshot_id contains unsupported characters")
        return value

    @field_validator("data_as_of", "range_start", "range_end")
    @classmethod
    def aware_timestamp(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("timestamps must contain an offset")
        return value

    @model_validator(mode="after")
    def valid_envelope(self) -> HealthBatchInput:
        for record in self.records:
            if record.type != self.record_type:
                raise ValueError("record type does not match the batch envelope")
            if record.data_origin != self.data_origin:
                raise ValueError("record origin does not match the batch envelope")
        if self.mode == "snapshot":
            if (
                self.snapshot_id is None
                or self.range_start is None
                or self.range_end is None
                or self.page_index is None
                or self.final_page is None
            ):
                raise ValueError("snapshot metadata is incomplete")
            if self.range_end <= self.range_start:
                raise ValueError("snapshot range is invalid")
            snapshot_range = self.range_end - self.range_start
            if snapshot_range > timedelta(days=31):
                is_standalone_empty_snapshot = (
                    not self.records and self.final_page is True and self.page_index == 0
                )
                if not is_standalone_empty_snapshot:
                    raise ValueError("snapshot range cannot exceed 31 days")
                if snapshot_range > timedelta(days=366 * 50):
                    raise ValueError("empty snapshot range cannot exceed 50 years")
            if any(record.deleted for record in self.records):
                raise ValueError("snapshot pages cannot contain deletion records")
            for record in self.records:
                assert record.start_time is not None
                record_end = record.end_time or record.start_time
                if record.start_time >= self.range_end or record_end < self.range_start:
                    raise ValueError("snapshot record is outside the requested range")
        elif any(
            value is not None
            for value in (
                self.snapshot_id,
                self.range_start,
                self.range_end,
                self.page_index,
                self.final_page,
            )
        ):
            raise ValueError("changes batches cannot contain snapshot metadata")
        return self


class BatchAcceptedResponse(BaseModel):
    status: Literal["accepted"] = "accepted"
    batch_id: str
    idempotent: bool = False
    record_count: int
    upserted_count: int
    deleted_count: int
    reconciled_count: int
    data_as_of: datetime


MI_FITNESS_RECORD_TYPES: frozenset[str] = frozenset(
    (
        "steps",
        "distance",
        "active_calories",
        "exercise",
        "sleep",
        "heart_rate",
        "resting_heart_rate",
        "hrv_rmssd",
        "oxygen_saturation",
        "vo2_max",
    )
)


class MiFitnessBatchInput(BaseModel):
    """One immutable page of an Android-normalised Xiaomi Cloud snapshot."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_version: Literal[1] = 1
    batch_id: str | None = Field(default=None, min_length=1, max_length=128)
    record_type: HealthRecordType
    data_as_of: datetime
    source_data_as_of: datetime | None = None
    range_start: datetime
    range_end: datetime
    snapshot_id: str = Field(min_length=1, max_length=128)
    page_index: int = Field(ge=0, le=100_000)
    final_page: bool
    records: list[HealthRecordInput] = Field(default_factory=list, max_length=2_000)

    @field_validator("snapshot_id", "batch_id")
    @classmethod
    def safe_id(cls, value: str | None) -> str | None:
        if value is not None and not _SAFE_ID.fullmatch(value):
            raise ValueError("identifier contains unsupported characters")
        return value

    @field_validator("data_as_of", "source_data_as_of", "range_start", "range_end")
    @classmethod
    def aware_cloud_timestamp(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("timestamps must contain an offset")
        return value

    @model_validator(mode="after")
    def valid_cloud_snapshot(self) -> MiFitnessBatchInput:
        if self.record_type not in MI_FITNESS_RECORD_TYPES:
            raise ValueError("record type is not available from Xiaomi Cloud")
        if self.range_end <= self.range_start:
            raise ValueError("snapshot range is invalid")
        span = self.range_end - self.range_start
        if span > timedelta(days=31):
            standalone_empty = not self.records and self.final_page and self.page_index == 0
            if not standalone_empty or span > timedelta(days=366 * 50):
                raise ValueError("cloud snapshot range is too long")
        for record in self.records:
            if record.deleted:
                raise ValueError("cloud snapshots cannot contain deletions")
            if record.type != self.record_type:
                raise ValueError("record type does not match the batch envelope")
            if record.data_origin != "xiaomi_cloud":
                raise ValueError("cloud record origin is invalid")
            assert record.start_time is not None
            record_end = record.end_time or record.start_time
            if record.start_time >= self.range_end or record_end < self.range_start:
                raise ValueError("snapshot record is outside the requested range")
        return self


class MiFitnessBatchAcceptedResponse(BaseModel):
    status: Literal["accepted"] = "accepted"
    batch_id: str
    idempotent: bool = False
    record_count: int = 0
    changed_count: int = 0
    reconciled_count: int = 0
    coverage_published: bool = False
    active: bool = False
    data_as_of: datetime


class MiFitnessStatusInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_version: Literal[1] = 1
    enabled: bool
    status: Literal[
        "pending",
        "success",
        "auth_required",
        "rate_limited",
        "network_error",
        "disabled",
    ]
    account_fingerprint: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    region: Literal["cn", "sg", "us", "de", "ru", "i2"] | None = None
    data_as_of: datetime | None = None
    error_code: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]{0,63}$")

    @field_validator("data_as_of")
    @classmethod
    def aware_status_timestamp(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("timestamps must contain an offset")
        return value

    @model_validator(mode="after")
    def valid_status(self) -> MiFitnessStatusInput:
        if not self.enabled:
            if self.status != "disabled":
                raise ValueError("disabled source must report disabled status")
            return self
        if self.status == "disabled":
            raise ValueError("enabled source cannot report disabled status")
        if self.account_fingerprint is None or self.region is None:
            raise ValueError("enabled source requires account and region")
        if self.status == "success" and self.error_code is not None:
            raise ValueError("successful status cannot contain an error")
        return self


class MiFitnessStatusResponse(BaseModel):
    status: Literal[
        "pending",
        "success",
        "auth_required",
        "rate_limited",
        "network_error",
        "disabled",
    ]
    enabled: bool
    active: bool
    activation_missing_types: list[str] = Field(default_factory=list)
