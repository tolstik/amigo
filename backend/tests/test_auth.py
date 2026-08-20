from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import Response
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.auth import (
    AI_DATA_CONSENT_VERSION,
    CSRF_COOKIE,
    SESSION_COOKIE,
    _set_auth_cookies,
    set_password,
)
from app.auth_models import AuthSession
from app.config import Settings, get_settings
from app.db import get_db
from app.main import app


def settings() -> Settings:
    return Settings(
        database_url="sqlite+pysqlite:///:memory:",
        env="test",
        public_url="https://testserver/",
        ai_enabled=False,
    )


def client_for(db):
    def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_settings] = settings
    return TestClient(app, base_url="https://testserver")


def login(client: TestClient, password: str = "correct horse battery staple"):
    return client.post(
        "/api/v1/auth/login",
        headers={"Origin": "https://testserver"},
        json={"username": "amigo", "password": password},
    )


def test_login_session_logout_and_cookie_storage(db):
    password = "correct horse battery staple"
    user = set_password(db, "amigo", password)
    assert user.password_hash.startswith("$argon2id$")
    assert password not in user.password_hash
    try:
        with client_for(db) as client:
            failed = login(client, "a different long password")
            assert failed.status_code == 401
            accepted = login(client, password)
            assert accepted.status_code == 200
            assert accepted.json()["authenticated"] is True
            assert client.get("/api/v1/auth/session").status_code == 200
            stored = db.scalar(select(AuthSession))
            assert stored is not None
            assert len(stored.token_hash) == 64
            assert password not in stored.token_hash
            assert client.post("/api/v1/auth/logout").status_code == 403
            csrf = client.cookies.get(CSRF_COOKIE)
            assert client.post(
                "/api/v1/auth/logout",
                headers={"Origin": "https://testserver", "X-CSRF-Token": csrf},
            ).status_code == 204
            assert client.get("/api/v1/auth/session").status_code == 401
    finally:
        app.dependency_overrides.clear()


def test_cookie_contract_uses_public_prefix_and_secure_flags():
    response = Response()
    configured = Settings(
        database_url="sqlite+pysqlite:///:memory:",
        env="production",
        public_url="https://amigo.tolstik.ru/amigo/",
        ai_enabled=True,
    )
    _set_auth_cookies(response, configured, "session-token", "csrf-token")
    values = response.headers.getlist("set-cookie")
    assert any(f"{SESSION_COOKIE}=session-token" in value and "HttpOnly" in value for value in values)
    assert any(f"{CSRF_COOKIE}=csrf-token" in value and "HttpOnly" not in value for value in values)
    assert all("Path=/amigo/" in value and "SameSite=strict" in value and "Secure" in value for value in values)
    assert all("Max-Age=7776000" in value for value in values)


def test_csrf_origin_profile_expiry_and_password_rotation(db, monkeypatch):
    set_password(db, "amigo", "correct horse battery staple")
    monkeypatch.setattr("app.ai_snapshot.enqueue_current_analysis", lambda *_args, **_kwargs: None)
    try:
        with client_for(db) as client:
            assert login(client).status_code == 200
            csrf = client.cookies.get(CSRF_COOKIE)
            assert client.patch(
                "/api/v1/profile",
                headers={"Origin": "https://evil.example", "X-CSRF-Token": csrf},
                json={"birth_date": "1990-04-12"},
            ).status_code == 403
            profile = client.patch(
                "/api/v1/profile",
                headers={"Origin": "https://testserver", "X-CSRF-Token": csrf},
                json={
                    "birth_date": "1990-04-12",
                    "reference_sex": "male",
                    "accept_ai_data_processing": True,
                },
            )
            assert profile.status_code == 200
            assert profile.json()["ai_data_consent_version"] == AI_DATA_CONSENT_VERSION
            stored = db.scalar(select(AuthSession).where(AuthSession.revoked_at.is_(None)))
            assert stored is not None
            stored.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
            db.commit()
            assert client.get("/api/v1/auth/session").status_code == 401

            assert login(client).status_code == 200
            set_password(db, "amigo", "new correct horse battery")
            assert client.get("/api/v1/auth/session").status_code == 401
            assert login(client).status_code == 401
            assert login(client, "new correct horse battery").status_code == 200
    finally:
        app.dependency_overrides.clear()


def test_health_and_new_private_routes_fail_closed_without_session(db):
    try:
        with client_for(db) as client:
            for path in (
                "/api/v1/overview",
                "/api/v1/export/weight.csv?range=all",
                "/api/v1/labs/documents",
                "/api/v1/labs/summary",
                "/api/v1/assistant/messages",
                "/api/v1/profile",
            ):
                assert client.get(path).status_code == 401, path
            assert client.post(
                "/api/v1/labs/documents/00000000-0000-0000-0000-000000000000/results"
            ).status_code == 401
    finally:
        app.dependency_overrides.clear()
