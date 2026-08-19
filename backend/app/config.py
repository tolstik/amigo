from __future__ import annotations

from functools import lru_cache
from datetime import time
import os
from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic import AliasChoices, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="AMIGO_",
        env_file=".env",
        extra="ignore",
        case_sensitive=False,
        populate_by_name=True,
    )

    database_url: str = Field(
        default="postgresql+psycopg://amigo@localhost/amigo",
        validation_alias=AliasChoices("AMIGO_DATABASE_URL", "DATABASE_URL"),
    )
    timezone: str = "Europe/Moscow"
    public_url: str = "https://amigo.tolstik.ru/amigo/"
    static_dir: Path = Path("/app/static")
    log_level: str = "INFO"

    token_encryption_key: str | None = Field(default=None, repr=False)
    withings_client_id: str | None = None
    withings_client_secret: str | None = Field(default=None, repr=False)
    withings_callback_url: str | None = None
    withings_access_token: str | None = Field(default=None, repr=False)
    withings_refresh_token: str | None = Field(default=None, repr=False)
    withings_api_url: str = "https://wbsapi.withings.net"
    withings_oauth_url: str = "https://wbsapi.withings.net/v2/oauth2"

    telegram_bot_token: str | None = Field(default=None, repr=False)
    telegram_chat_id: str | None = None
    telegram_api_url: str = "https://api.telegram.org"

    sync_interval_seconds: int = 300
    outbox_poll_seconds: int = 10
    withings_overlap_seconds: int = 900
    new_group_settle_seconds: int = Field(
        default=120,
        validation_alias=AliasChoices(
            "AMIGO_NEW_GROUP_SETTLE_SECONDS", "AMIGO_NOTIFICATION_DEBOUNCE_SECONDS"
        ),
    )
    weekly_digest_day: str = "mon"
    weekly_digest_time: time = time(8, 0)
    worker_once: bool = False

    @model_validator(mode="after")
    def load_docker_secrets(self) -> Settings:
        """Load configured Docker secrets without ever putting their values in logs."""
        secret_files = {
            "token_encryption_key": "AMIGO_ENCRYPTION_KEY_FILE",
            "withings_client_id": "WITHINGS_CLIENT_ID_FILE",
            "withings_client_secret": "WITHINGS_CLIENT_SECRET_FILE",
            "withings_access_token": "WITHINGS_ACCESS_TOKEN_FILE",
            "withings_refresh_token": "WITHINGS_REFRESH_TOKEN_FILE",
            "telegram_bot_token": "TELEGRAM_BOT_TOKEN_FILE",
            "telegram_chat_id": "TELEGRAM_CHAT_ID_FILE",
        }
        for field_name, environment_name in secret_files.items():
            if getattr(self, field_name):
                continue
            path = os.getenv(environment_name)
            if path:
                value = Path(path).read_text(encoding="utf-8").strip()
                if value:
                    object.__setattr__(self, field_name, value)
        database = make_url(self.database_url)
        password_file = os.getenv("DATABASE_PASSWORD_FILE") or os.getenv("POSTGRES_PASSWORD_FILE")
        if database.password is None and password_file:
            password = Path(password_file).read_text(encoding="utf-8").strip()
            if password:
                object.__setattr__(
                    self,
                    "database_url",
                    database.set(password=password).render_as_string(hide_password=False),
                )
        return self

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        ZoneInfo(value)
        return value

    @field_validator("sync_interval_seconds", "outbox_poll_seconds")
    @classmethod
    def positive_interval(cls, value: int) -> int:
        if value < 1:
            raise ValueError("interval must be positive")
        return value

    @field_validator("weekly_digest_day")
    @classmethod
    def valid_weekday(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in {"mon", "tue", "wed", "thu", "fri", "sat", "sun"}:
            raise ValueError("weekly digest day must be a three-letter weekday")
        return normalized

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)


@lru_cache
def get_settings() -> Settings:
    return Settings()
