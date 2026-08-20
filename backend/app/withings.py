from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import time
from typing import Any, Iterable

import httpx
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .config import Settings, get_settings
from .crypto import SecretCipher
from .models import (
    Measurement,
    MeasurementGroup,
    Outbox,
    ProviderCredential,
    SyncState,
    utcnow,
)


MEASUREMENT_TYPES: dict[int, tuple[str, str]] = {
    1: ("weight", "kg"),
    4: ("height", "m"),
    5: ("fat_free_mass", "kg"),
    6: ("fat_percent", "%"),
    8: ("fat_mass", "kg"),
    9: ("diastolic", "mmHg"),
    10: ("systolic", "mmHg"),
    11: ("pulse", "bpm"),
    12: ("temperature", "°C"),
    54: ("spo2", "%"),
    71: ("body_temperature", "°C"),
    76: ("muscle_mass", "kg"),
    77: ("hydration", "kg"),
    88: ("bone_mass", "kg"),
}


class WithingsError(RuntimeError):
    pass


@dataclass(frozen=True)
class SyncResult:
    pages: int
    groups_seen: int
    groups_created: int
    groups_updated: int
    notifications_enqueued: int
    cursor: int


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


class CredentialStore:
    def __init__(self, db: Session, settings: Settings):
        self.db = db
        self.settings = settings
        self.cipher = SecretCipher(settings.token_encryption_key)

    def bootstrap(self) -> ProviderCredential:
        credential = self.db.get(ProviderCredential, "withings")
        if credential:
            return credential
        if not self.settings.withings_access_token or not self.settings.withings_refresh_token:
            raise WithingsError("Withings credentials are not initialized")
        credential = ProviderCredential(
            provider="withings",
            access_token_encrypted=self.cipher.encrypt(self.settings.withings_access_token),
            refresh_token_encrypted=self.cipher.encrypt(self.settings.withings_refresh_token),
            expires_at=utcnow() + timedelta(minutes=30),
        )
        self.db.add(credential)
        self.db.flush()
        return credential

    def access_token(self, http: httpx.Client) -> str:
        credential = self.bootstrap()
        if _as_utc(credential.expires_at) > utcnow() + timedelta(seconds=90):
            return self.cipher.decrypt(credential.access_token_encrypted)
        return self.refresh(http, credential)

    def refresh(self, http: httpx.Client, credential: ProviderCredential | None = None) -> str:
        credential = credential or self.bootstrap()
        if not self.settings.withings_client_id or not self.settings.withings_client_secret:
            raise WithingsError("Withings OAuth client is not configured")
        response = http.post(
            self.settings.withings_oauth_url,
            data={
                "action": "requesttoken",
                "grant_type": "refresh_token",
                "client_id": self.settings.withings_client_id,
                "client_secret": self.settings.withings_client_secret,
                "refresh_token": self.cipher.decrypt(credential.refresh_token_encrypted),
            },
        )
        payload = _checked_payload(response)
        body = payload.get("body", payload)
        try:
            access_token = str(body["access_token"])
            refresh_token = str(body.get("refresh_token") or self.cipher.decrypt(credential.refresh_token_encrypted))
            expires_in = int(body.get("expires_in", 10800))
        except (KeyError, TypeError, ValueError) as exc:
            raise WithingsError("Withings returned an invalid OAuth response") from exc
        credential.access_token_encrypted = self.cipher.encrypt(access_token)
        credential.refresh_token_encrypted = self.cipher.encrypt(refresh_token)
        credential.expires_at = utcnow() + timedelta(seconds=max(60, expires_in))
        credential.account_id = str(body.get("userid")) if body.get("userid") is not None else credential.account_id
        # Withings rotates refresh tokens. Persist the new pair immediately so a
        # later measurement-page failure cannot roll it back and strand sync.
        self.db.commit()
        return access_token


def _checked_payload(response: httpx.Response) -> dict[str, Any]:
    try:
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise WithingsError("Withings request failed") from exc
    if not isinstance(payload, dict):
        raise WithingsError("Withings returned a malformed response")
    status = int(payload.get("status", 0))
    if status != 0:
        raise WithingsError(f"Withings API status {status}")
    return payload


class WithingsClient:
    def __init__(
        self,
        db: Session,
        settings: Settings | None = None,
        http: httpx.Client | None = None,
    ):
        self.db = db
        self.settings = settings or get_settings()
        self._owns_http = http is None
        self.http = http or httpx.Client(timeout=httpx.Timeout(30, connect=10))
        self.credentials = CredentialStore(db, self.settings)

    def close(self) -> None:
        if self._owns_http:
            self.http.close()

    def __enter__(self) -> WithingsClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _page(self, params: dict[str, Any]) -> dict[str, Any]:
        token = self.credentials.access_token(self.http)
        response = self.http.get(
            f"{self.settings.withings_api_url.rstrip('/')}/measure",
            params={"action": "getmeas", "category": 1, **params},
            headers={"Authorization": f"Bearer {token}"},
        )
        # Withings may report an expired token in its JSON status while returning 200.
        try:
            payload = _checked_payload(response)
        except WithingsError:
            raw: dict[str, Any] = {}
            try:
                raw = response.json()
            except ValueError:
                pass
            if int(raw.get("status", 0) or 0) in {100, 101, 102, 401}:
                token = self.credentials.refresh(self.http)
                response = self.http.get(
                    f"{self.settings.withings_api_url.rstrip('/')}/measure",
                    params={"action": "getmeas", "category": 1, **params},
                    headers={"Authorization": f"Bearer {token}"},
                )
                payload = _checked_payload(response)
            else:
                raise
        body = payload.get("body")
        if not isinstance(body, dict):
            raise WithingsError("Withings response has no body")
        return body

    def iter_groups(
        self,
        *,
        full: bool = False,
        lastupdate: int | None = None,
        startdate: int | None = None,
        enddate: int | None = None,
    ) -> Iterable[tuple[list[dict[str, Any]], dict[str, Any]]]:
        params: dict[str, Any] = {}
        if full:
            params["startdate"] = 0 if startdate is None else startdate
        elif lastupdate is not None:
            params["lastupdate"] = lastupdate
        elif startdate is not None:
            params["startdate"] = startdate
        if enddate is not None:
            params["enddate"] = enddate
        seen_offsets: set[int] = set()
        while True:
            body = self._page(params)
            groups = body.get("measuregrps", [])
            if not isinstance(groups, list):
                raise WithingsError("Withings measure groups are malformed")
            yield groups, body
            if not body.get("more"):
                return
            try:
                offset = int(body["offset"])
            except (KeyError, TypeError, ValueError) as exc:
                raise WithingsError("Withings pagination cursor is missing") from exc
            if offset in seen_offsets:
                raise WithingsError("Withings pagination cursor did not advance")
            seen_offsets.add(offset)
            params["offset"] = offset

    def sync(
        self,
        *,
        full: bool = False,
        suppress_notifications: bool | None = None,
        reconcile_days: int | None = None,
    ) -> SyncResult:
        started_cursor = int(time.time())
        state = self.db.get(SyncState, "withings")
        if state is None:
            state = SyncState(provider="withings")
            self.db.add(state)
            self.db.flush()
        initial = not state.initial_import_done
        effective_full = full or initial
        if effective_full:
            suppress_notifications = True
        elif suppress_notifications is None:
            suppress_notifications = False
        lastupdate: int | None = None
        startdate: int | None = None
        if reconcile_days is not None and not effective_full:
            startdate = started_cursor - reconcile_days * 86400
        elif not effective_full and state.lastupdate is not None:
            lastupdate = max(0, state.lastupdate - self.settings.withings_overlap_seconds)

        pages = seen = created = updated = notifications = 0
        server_cursor = started_cursor
        try:
            for groups, body in self.iter_groups(
                full=effective_full,
                lastupdate=lastupdate,
                startdate=startdate,
                enddate=started_cursor if effective_full or reconcile_days is not None else None,
            ):
                pages += 1
                for raw_group in groups:
                    seen += 1
                    was_created, was_changed, event_types = upsert_measurement_group(
                        self.db, raw_group
                    )
                    created += int(was_created)
                    updated += int(was_changed and not was_created)
                    if was_created and not suppress_notifications:
                        notifications += enqueue_group_notifications(
                            self.db,
                            raw_group,
                            event_types,
                            delay_seconds=self.settings.new_group_settle_seconds,
                        )
                for cursor_name in ("updatetime", "lastupdate"):
                    if body.get(cursor_name) is not None:
                        try:
                            server_cursor = max(server_cursor, int(body[cursor_name]))
                        except (TypeError, ValueError):
                            pass
                self.db.flush()
            state.lastupdate = server_cursor
            state.last_success_at = utcnow()
            state.last_error = None
            if effective_full:
                state.initial_import_done = True
            self.db.commit()
        except Exception as exc:
            self.db.rollback()
            state = self.db.get(SyncState, "withings") or SyncState(provider="withings")
            state.last_error = str(exc)[:2000]
            self.db.add(state)
            self.db.commit()
            raise
        return SyncResult(pages, seen, created, updated, notifications, server_cursor)


def _scaled_measure(raw: dict[str, Any]) -> Decimal:
    try:
        return Decimal(str(raw["value"])) * (Decimal(10) ** int(raw.get("unit", 0)))
    except (KeyError, TypeError, ValueError) as exc:
        raise WithingsError("invalid Withings measure value") from exc


def upsert_measurement_group(
    db: Session, raw_group: dict[str, Any], provider: str = "withings"
) -> tuple[bool, bool, set[str]]:
    try:
        provider_id = str(raw_group["grpid"])
        measured_at = datetime.fromtimestamp(int(raw_group["date"]), tz=timezone.utc)
    except (KeyError, TypeError, ValueError, OSError) as exc:
        raise WithingsError("invalid Withings measurement group") from exc
    group = db.scalar(
        select(MeasurementGroup).where(
            MeasurementGroup.provider == provider,
            MeasurementGroup.provider_group_id == provider_id,
        )
    )
    created = group is None
    # The overlap window intentionally returns already imported groups. Treat
    # an identical provider payload as unchanged so downstream AI analysis is
    # not regenerated every five minutes for the same measurements.
    if group is not None and group.raw_payload == raw_group:
        return False, False, set()
    if group is None:
        group = MeasurementGroup(provider=provider, provider_group_id=provider_id, measured_at=measured_at)
        db.add(group)
        db.flush()
    group.measured_at = measured_at
    group.source_timezone = raw_group.get("timezone")
    group.source = provider
    group.raw_payload = raw_group
    modified = raw_group.get("modified")
    if modified is not None:
        try:
            group.provider_updated_at = datetime.fromtimestamp(int(modified), tz=timezone.utc)
        except (TypeError, ValueError, OSError):
            group.provider_updated_at = None
    if not created:
        db.execute(delete(Measurement).where(Measurement.group_id == group.id))
    event_types: set[str] = set()
    measurement_kinds: set[str] = set()
    raw_measures = raw_group.get("measures", [])
    if not isinstance(raw_measures, list):
        raise WithingsError("invalid Withings measures")
    type_counts: dict[int, int] = {}
    for raw in raw_measures:
        try:
            type_id = int(raw["type"])
        except (KeyError, TypeError, ValueError) as exc:
            raise WithingsError("invalid Withings measure type") from exc
        kind, unit = MEASUREMENT_TYPES.get(type_id, (f"withings_type_{type_id}", "raw"))
        measurement_kinds.add(kind)
        raw_index = type_counts.get(type_id, 0)
        type_counts[type_id] = raw_index + 1
        db.add(
            Measurement(
                group_id=group.id,
                kind=kind,
                value=_scaled_measure(raw),
                unit=unit,
                raw_type=type_id,
                raw_index=raw_index,
            )
        )
    if "weight" in measurement_kinds:
        event_types.add("measurement.weight")
    if {"systolic", "diastolic"} <= measurement_kinds:
        event_types.add("measurement.pressure")
    return created, True, event_types


def enqueue_group_notifications(
    db: Session,
    raw_group: dict[str, Any],
    event_types: set[str],
    delay_seconds: int,
) -> int:
    provider_id = str(raw_group["grpid"])
    count = 0
    for event_type in event_types:
        event_key = f"withings:{provider_id}:{event_type}"
        exists = db.scalar(select(Outbox.id).where(Outbox.event_key == event_key))
        if exists is not None:
            continue
        db.add(
            Outbox(
                event_key=event_key,
                event_type=event_type,
                payload={"provider": "withings", "provider_group_id": provider_id},
                available_at=utcnow() + timedelta(seconds=delay_seconds),
            )
        )
        db.flush()
        count += 1
    return count
