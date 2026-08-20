from __future__ import annotations

from datetime import datetime, timedelta, timezone

from cryptography.fernet import Fernet
import httpx
from sqlalchemy import func, select

from app.config import Settings
from app.legacy import import_legacy_weight_file
from app.models import Measurement, MeasurementGroup, ProviderCredential
from app.withings import WithingsClient


def test_full_withings_sync_paginates_scales_and_deduplicates(db):
    key = Fernet.generate_key().decode()
    settings = Settings(
        database_url="sqlite+pysqlite:///:memory:",
        token_encryption_key=key,
        withings_api_url="https://withings.test",
        withings_oauth_url="https://withings.test/oauth",
        withings_client_id="id",
        withings_client_secret="secret",
        new_group_settle_seconds=0,
    )
    from app.crypto import SecretCipher

    cipher = SecretCipher(key)
    db.add(
        ProviderCredential(
            provider="withings",
            access_token_encrypted=cipher.encrypt("access"),
            refresh_token_encrypted=cipher.encrypt("refresh"),
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
    )
    db.commit()

    value_adjustment = 0

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer access"
        offset = request.url.params.get("offset")
        group_id = 1 if offset is None else 2
        body = {
            "measuregrps": [
                {
                    "grpid": group_id,
                    "date": 1786773600 + group_id,
                    "timezone": "Europe/Moscow",
                    "measures": [
                        {
                            "type": 1,
                            "value": 127030 - group_id * 100 + value_adjustment,
                            "unit": -3,
                        }
                    ],
                }
            ],
            "more": 1 if offset is None else 0,
        }
        if offset is None:
            body["offset"] = 1
        return httpx.Response(200, json={"status": 0, "body": body})

    http = httpx.Client(transport=httpx.MockTransport(handler))
    with WithingsClient(db, settings, http) as client:
        result = client.sync(full=True, suppress_notifications=True)
    assert result.pages == 2
    assert result.groups_created == 2
    assert db.scalar(select(func.count()).select_from(MeasurementGroup)) == 2
    values = list(db.scalars(select(Measurement.value).order_by(Measurement.value)))
    assert float(values[0]) == 126.83

    # An overlapping full import updates the same provider groups instead of duplicating them.
    with WithingsClient(db, settings, http) as client:
        second = client.sync(full=True, suppress_notifications=True)
    assert second.groups_created == 0
    assert second.groups_updated == 0
    assert db.scalar(select(func.count()).select_from(MeasurementGroup)) == 2

    # A provider-side correction remains a real update and replaces the
    # normalized values without duplicating the group.
    value_adjustment = 100
    with WithingsClient(db, settings, http) as client:
        corrected = client.sync(full=True, suppress_notifications=True)
    assert corrected.groups_created == 0
    assert corrected.groups_updated == 2
    corrected_values = list(db.scalars(select(Measurement.value).order_by(Measurement.value)))
    assert float(corrected_values[0]) == 126.93


def test_legacy_weight_file_uses_utc_scale_and_skips_withings_duplicate(db, add_group, tmp_path):
    measured = datetime(2026, 8, 15, 5, tzinfo=timezone.utc)
    add_group("withings-1", measured, {"weight": (127.03, "kg")}, provider="withings")
    source = tmp_path / "legacy.tsv"
    source.write_text("2026-08-15 05:00:00\t127030\n2026-08-16 05:00:00\t126500\n", encoding="utf-8")
    result = import_legacy_weight_file(db, source, timezone.utc, scale=0.001)
    assert result.duplicates_skipped == 1
    assert result.groups_created == 1
    legacy = db.scalar(select(MeasurementGroup).where(MeasurementGroup.provider == "legacy"))
    assert legacy.measured_at.replace(tzinfo=timezone.utc) == datetime(2026, 8, 16, 5, tzinfo=timezone.utc)
    assert float(legacy.measurements[0].value) == 126.5
