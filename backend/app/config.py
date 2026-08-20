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
    env: str = "development"
    timezone: str = "Europe/Moscow"
    public_url: str = "https://amigo.tolstik.ru/amigo/"
    static_dir: Path = Path("/app/static")
    log_level: str = "INFO"
    user_height_cm: float = 176.0
    auth_username: str = "amigo"
    auth_session_days: int = 90
    lab_storage_dir: Path = Path("/srv/amigo/data/lab-files")
    lab_parser_url: str = "http://lab-parser:8085"
    lab_parser_timeout_seconds: int = 180
    assistant_max_attempts: int = 2
    android_apk_path: Path = Path("/android/amigo-sync.apk")
    android_apk_version_code: int = 5
    android_apk_version_name: str = "1.2.0"

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
    outbox_poll_seconds: int = 60
    withings_overlap_seconds: int = 900
    new_group_settle_seconds: int = Field(
        default=120,
        validation_alias=AliasChoices(
            "AMIGO_NEW_GROUP_SETTLE_SECONDS", "AMIGO_NOTIFICATION_DEBOUNCE_SECONDS"
        ),
    )
    weekly_digest_day: str = "mon"
    weekly_digest_time: time = time(9, 0)
    daily_digest_time: time = time(9, 0)
    worker_once: bool = False

    # AI analysis runs out of process. The public web application never calls
    # the gateway directly; these settings are consumed by the dedicated AI
    # queue worker.
    ai_enabled: bool = False
    ai_gateway_url: str = "http://ai-gateway:8090"
    ai_gateway_timeout_seconds: int = 90
    ai_poll_seconds: int = 60
    ai_debounce_seconds: int = 300
    ai_activity_min_interval_seconds: int = 3600
    ai_stale_seconds: int = 86400
    ai_lease_seconds: int = 180
    ai_max_attempts: int = 4
    ai_backoff_base_seconds: int = 60

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

    @field_validator("env")
    @classmethod
    def valid_environment(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in {"development", "test", "production"}:
            raise ValueError("environment must be development, test, or production")
        return normalized

    @field_validator(
        "sync_interval_seconds",
        "outbox_poll_seconds",
        "ai_gateway_timeout_seconds",
        "ai_poll_seconds",
        "ai_stale_seconds",
        "ai_lease_seconds",
        "ai_max_attempts",
        "ai_backoff_base_seconds",
        "auth_session_days",
        "lab_parser_timeout_seconds",
        "assistant_max_attempts",
        "android_apk_version_code",
    )
    @classmethod
    def positive_interval(cls, value: int) -> int:
        if value < 1:
            raise ValueError("interval must be positive")
        return value

    @field_validator("ai_debounce_seconds", "ai_activity_min_interval_seconds")
    @classmethod
    def non_negative_interval(cls, value: int) -> int:
        if value < 0:
            raise ValueError("interval must not be negative")
        return value

    @field_validator("user_height_cm")
    @classmethod
    def valid_user_height(cls, value: float) -> float:
        if not 100 <= value <= 250:
            raise ValueError("user height must be between 100 and 250 cm")
        return round(value, 1)

    @field_validator("ai_gateway_url", "lab_parser_url")
    @classmethod
    def valid_ai_gateway_url(cls, value: str) -> str:
        normalized = value.strip().rstrip("/")
        if not normalized.startswith(("http://", "https://")):
            raise ValueError("AI gateway URL must use HTTP or HTTPS")
        return normalized

    @model_validator(mode="after")
    def enforce_production_ai_boundary(self) -> Settings:
        if self.env == "production":
            if not self.ai_enabled:
                raise ValueError("AI analysis must be enabled in production")
            if self.ai_gateway_url != "http://ai-gateway:8090":
                raise ValueError("production AI gateway must use the isolated Compose service")
            if self.lab_parser_url != "http://lab-parser:8085":
                raise ValueError("production lab parser must use the isolated Compose service")
            if (
                self.weekly_digest_day != "mon"
                or self.weekly_digest_time != time(9, 0)
                or self.daily_digest_time != time(9, 0)
            ):
                raise ValueError("production Telegram digests must use the 09:00 Monday contract")
        return self

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
