from __future__ import annotations

import pytest
from pydantic import ValidationError
from sqlalchemy.engine import make_url

from app.config import Settings


def test_docker_secret_files_and_database_password_are_loaded(monkeypatch, tmp_path):
    values = {
        "AMIGO_ENCRYPTION_KEY_FILE": "encryption-key",
        "WITHINGS_CLIENT_ID_FILE": "client-id",
        "WITHINGS_CLIENT_SECRET_FILE": "client-secret",
        "WITHINGS_ACCESS_TOKEN_FILE": "access-token",
        "WITHINGS_REFRESH_TOKEN_FILE": "refresh-token",
        "TELEGRAM_BOT_TOKEN_FILE": "bot-token",
        "TELEGRAM_CHAT_ID_FILE": "chat-id",
        "POSTGRES_PASSWORD_FILE": "p@ss word",
    }
    for environment_name, value in values.items():
        path = tmp_path / environment_name.lower()
        path.write_text(value + "\n", encoding="utf-8")
        monkeypatch.setenv(environment_name, str(path))
    settings = Settings(database_url="postgresql+psycopg://amigo@db:5432/amigo")
    assert settings.token_encryption_key == "encryption-key"
    assert settings.withings_client_id == "client-id"
    assert settings.withings_client_secret == "client-secret"
    assert settings.withings_access_token == "access-token"
    assert settings.withings_refresh_token == "refresh-token"
    assert settings.telegram_bot_token == "bot-token"
    assert settings.telegram_chat_id == "chat-id"
    assert make_url(settings.database_url).password == "p@ss word"


def test_production_requires_the_isolated_ai_gateway():
    settings = Settings(
        env="production",
        ai_enabled=True,
        ai_gateway_url="http://ai-gateway:8090/",
    )
    assert settings.ai_gateway_url == "http://ai-gateway:8090"

    with pytest.raises(ValidationError, match="must be enabled"):
        Settings(env="production", ai_enabled=False)

    with pytest.raises(ValidationError, match="isolated Compose service"):
        Settings(
            env="production",
            ai_enabled=True,
            ai_gateway_url="https://example.invalid/analyze",
        )

    with pytest.raises(ValidationError, match="09:00 Monday contract"):
        Settings(
            env="production",
            ai_enabled=True,
            weekly_digest_time="08:00",
        )
