from __future__ import annotations

from datetime import date
from hashlib import sha256

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.auth import CSRF_COOKIE, set_password
from app.config import Settings, get_settings
from app.db import get_db
from app.lab_models import StoredFile, StudyDocument
from app.main import app


def test_study_document_lifecycle_uses_database_original(db, tmp_path):
    configured = Settings(
        database_url="sqlite+pysqlite:///:memory:",
        env="test",
        public_url="https://testserver/",
        ai_enabled=False,
        lab_storage_dir=tmp_path,
    )

    def override_db():
        yield db

    def override_settings():
        return configured

    set_password(db, "amigo", "correct horse battery staple")
    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_settings] = override_settings
    content = b"%PDF-1.7\nsynthetic study fixture"
    try:
        with TestClient(app, base_url="https://testserver") as client:
            login = client.post(
                "/api/v1/auth/login",
                headers={"Origin": "https://testserver"},
                json={
                    "username": "amigo",
                    "password": "correct horse battery staple",
                },
            )
            assert login.status_code == 200
            csrf = client.cookies.get(CSRF_COOKIE)
            mutation_headers = {
                "Origin": "https://testserver",
                "X-CSRF-Token": csrf,
            }

            uploaded = client.post(
                "/api/v1/studies/uploads",
                headers=mutation_headers,
                data={"modality": "mri", "observed_on": "2026-08-20"},
                files={"file": ("study.pdf", content, "application/pdf")},
            )
            assert uploaded.status_code == 202
            document_id = uploaded.json()["id"]
            document = db.get(StudyDocument, document_id)
            assert document is not None
            assert document.observed_on == date(2026, 8, 20)
            stored = db.scalar(
                select(StoredFile).where(StoredFile.id == document.stored_file_id)
            )
            assert stored is not None
            assert bytes(stored.content) == content
            assert stored.file_sha256 == sha256(content).hexdigest()

            listed = client.get("/api/v1/studies/documents")
            assert listed.status_code == 200
            assert listed.json()["items"][0]["queue_position"] == 1

            viewed = client.get(f"/api/v1/studies/documents/{document_id}/view")
            assert viewed.status_code == 200
            assert viewed.headers["content-disposition"].startswith("inline;")
            assert viewed.content == content

            stored.content = b"tampered"
            db.commit()
            assert client.get(
                f"/api/v1/studies/documents/{document_id}/view"
            ).status_code == 404
            stored.content = content
            db.commit()

            patched = client.patch(
                f"/api/v1/studies/documents/{document_id}",
                headers=mutation_headers,
                json={
                    "title": "МРТ коленного сустава",
                    "findings": ["Суставные поверхности без видимых изменений."],
                    "conclusion": "Значимых изменений не выявлено.",
                },
            )
            assert patched.status_code == 200
            assert patched.json()["title"] == "МРТ коленного сустава"
            assert patched.json()["verified"] is False

            document.status = "complete"
            document.processing_stage = "complete"
            document.progress_percent = 100
            db.commit()
            confirmed = client.post(
                f"/api/v1/studies/documents/{document_id}/confirm",
                headers=mutation_headers,
            )
            assert confirmed.status_code == 200
            assert confirmed.json()["verified"] is True

            deleted = client.delete(
                f"/api/v1/studies/documents/{document_id}",
                headers=mutation_headers,
            )
            assert deleted.status_code == 204
            assert db.get(StudyDocument, document_id) is None
            assert db.get(StoredFile, stored.id) is None
    finally:
        app.dependency_overrides.clear()
