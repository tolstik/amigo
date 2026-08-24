from __future__ import annotations

from hashlib import sha256

from app.config import Settings
from app.update_api import _apk_digest, update_metadata


def test_update_metadata_is_bound_to_the_actual_apk_bytes(tmp_path):
    apk = tmp_path / "amigo-sync.apk"
    content = b"synthetic signed APK fixture"
    apk.write_bytes(content)
    _apk_digest.cache_clear()

    payload = update_metadata(
        Settings(
            database_url="sqlite+pysqlite:///:memory:",
            android_apk_path=apk,
            android_apk_version_code=10,
            android_apk_version_name="1.3.0",
        )
    )

    assert payload == {
        "version_code": 10,
        "version_name": "1.3.0",
        "size_bytes": len(content),
        "sha256": sha256(content).hexdigest(),
        "download_url": "/amigo/api/v1/app-update/apk",
    }
