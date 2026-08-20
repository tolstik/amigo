from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from functools import lru_cache
from hashlib import sha256
import secrets
from typing import Annotated, Literal
from urllib.parse import urlsplit
from uuid import uuid4

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from sqlalchemy import select, update
from sqlalchemy.orm import Session, joinedload

from .auth_models import AuthSession, AuthUser, UserProfile
from .config import Settings, get_settings
from .db import get_db


SESSION_COOKIE = "__Secure-amigo_session"
CSRF_COOKIE = "__Secure-amigo_csrf"
AI_DATA_CONSENT_VERSION = "amigo-ai-data-v1"
PASSWORD_MIN_LENGTH = 14
_password_hasher = PasswordHasher(
    time_cost=3,
    memory_cost=65_536,
    parallelism=2,
    hash_len=32,
    salt_len=16,
)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


Username = Annotated[
    str,
    StringConstraints(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9._-]+$"),
]


class LoginRequest(StrictModel):
    username: Username
    password: Annotated[str, StringConstraints(min_length=1, max_length=1024)]


class SessionResponse(StrictModel):
    authenticated: Literal[True] = True
    username: str
    expires_at: datetime


class ProfilePatch(StrictModel):
    birth_date: date | None = None
    reference_sex: Literal["male", "female", "unspecified"] | None = None
    accept_ai_data_processing: bool | None = None


class ProfileResponse(StrictModel):
    birth_date: date | None
    reference_sex: Literal["male", "female", "unspecified"] | None
    height_cm: float
    ai_data_consent_version: str | None
    ai_data_consent_at: datetime | None


@dataclass(frozen=True)
class AuthContext:
    user: AuthUser
    session: AuthSession


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _digest(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _canonical_origin(settings: Settings) -> str:
    parsed = urlsplit(settings.public_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise RuntimeError("AMIGO_PUBLIC_URL must contain an HTTP(S) origin")
    return f"{parsed.scheme}://{parsed.netloc}"


def _cookie_path(settings: Settings) -> str:
    path = urlsplit(settings.public_url).path or "/"
    return path if path.endswith("/") else f"{path}/"


def _secure_cookie(settings: Settings) -> bool:
    return settings.env == "production" or urlsplit(settings.public_url).scheme == "https"


def _require_origin(request: Request, settings: Settings) -> None:
    if request.headers.get("origin") != _canonical_origin(settings):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="invalid_origin")


@lru_cache(maxsize=1)
def _dummy_password_hash() -> str:
    return _password_hasher.hash("amigo-invalid-password-verifier")


def hash_password(password: str) -> str:
    if len(password) < PASSWORD_MIN_LENGTH:
        raise ValueError(f"password must contain at least {PASSWORD_MIN_LENGTH} characters")
    if len(password) > 1024:
        raise ValueError("password is too long")
    return _password_hasher.hash(password)


def set_password(db: Session, username: str, password: str) -> AuthUser:
    normalized = username.strip()
    if not normalized or len(normalized) > 80:
        raise ValueError("invalid username")
    password_hash = hash_password(password)
    now = datetime.now(timezone.utc)
    user = db.scalar(select(AuthUser).where(AuthUser.username == normalized))
    if user is None:
        user = AuthUser(
            username=normalized,
            password_hash=password_hash,
            active=True,
            password_changed_at=now,
        )
        db.add(user)
        db.flush()
    else:
        user.password_hash = password_hash
        user.active = True
        user.password_changed_at = now
        db.execute(
            update(AuthSession)
            .where(AuthSession.user_id == user.id, AuthSession.revoked_at.is_(None))
            .values(revoked_at=now)
        )
    db.commit()
    return user


def auth_is_configured(db: Session) -> bool:
    return (
        db.scalar(
            select(AuthUser.id).where(AuthUser.active.is_(True)).order_by(AuthUser.id).limit(1)
        )
        is not None
    )


def create_verification_session(db: Session, settings: Settings) -> tuple[str, str, datetime]:
    user = db.scalar(select(AuthUser).where(AuthUser.active.is_(True)).order_by(AuthUser.id).limit(1))
    if user is None:
        raise RuntimeError("authentication is not configured")
    now = datetime.now(timezone.utc)
    session_token = secrets.token_urlsafe(32)
    csrf_token = secrets.token_urlsafe(32)
    expires_at = now + timedelta(minutes=15)
    db.add(
        AuthSession(
            id=str(uuid4()),
            user_id=user.id,
            token_hash=_digest(session_token),
            csrf_hash=_digest(csrf_token),
            created_at=now,
            expires_at=expires_at,
        )
    )
    db.commit()
    return session_token, csrf_token, expires_at


def _verify_password(user: AuthUser | None, password: str) -> bool:
    password_hash = user.password_hash if user is not None else _dummy_password_hash()
    try:
        accepted = _password_hasher.verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError):
        return False
    return bool(accepted and user is not None and user.active)


def _set_auth_cookies(
    response: Response,
    settings: Settings,
    session_token: str,
    csrf_token: str,
) -> None:
    max_age = settings.auth_session_days * 24 * 60 * 60
    common = {
        "max_age": max_age,
        "path": _cookie_path(settings),
        "secure": _secure_cookie(settings),
        "samesite": "strict",
    }
    response.set_cookie(SESSION_COOKIE, session_token, httponly=True, **common)
    response.set_cookie(CSRF_COOKIE, csrf_token, httponly=False, **common)


def _clear_auth_cookies(response: Response, settings: Settings) -> None:
    common = {
        "path": _cookie_path(settings),
        "secure": _secure_cookie(settings),
        "samesite": "strict",
    }
    response.delete_cookie(SESSION_COOKIE, httponly=True, **common)
    response.delete_cookie(CSRF_COOKIE, httponly=False, **common)


def require_session(
    request: Request,
    db: Session = Depends(get_db),
) -> AuthContext:
    raw_token = request.cookies.get(SESSION_COOKIE)
    if not raw_token or len(raw_token) > 128:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="authentication_required",
        )
    stored = db.scalar(
        select(AuthSession)
        .options(joinedload(AuthSession.user))
        .where(AuthSession.token_hash == _digest(raw_token))
    )
    now = datetime.now(timezone.utc)
    if (
        stored is None
        or stored.revoked_at is not None
        or _aware_utc(stored.expires_at) <= now
        or not stored.user.active
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="authentication_required",
        )
    return AuthContext(user=stored.user, session=stored)


def require_csrf(
    request: Request,
    context: AuthContext = Depends(require_session),
    settings: Settings = Depends(get_settings),
) -> AuthContext:
    _require_origin(request, settings)
    header = request.headers.get("x-csrf-token")
    cookie = request.cookies.get(CSRF_COOKIE)
    if (
        not header
        or not cookie
        or not secrets.compare_digest(header, cookie)
        or not secrets.compare_digest(_digest(header), context.session.csrf_hash)
    ):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="invalid_csrf")
    return context


def get_or_create_profile(db: Session, settings: Settings) -> UserProfile:
    profile = db.get(UserProfile, 1)
    if profile is None:
        profile = UserProfile(id=1, height_cm=Decimal(str(settings.user_height_cm)))
        db.add(profile)
        db.commit()
        db.refresh(profile)
    return profile


def _profile_response(profile: UserProfile) -> ProfileResponse:
    sex = profile.reference_sex
    if sex not in {"male", "female", "unspecified", None}:
        sex = None
    return ProfileResponse(
        birth_date=profile.birth_date,
        reference_sex=sex,
        height_cm=float(profile.height_cm),
        ai_data_consent_version=profile.ai_data_consent_version,
        ai_data_consent_at=profile.ai_data_consent_at,
    )


auth_router = APIRouter(prefix="/api/v1/auth", tags=["auth"])
profile_router = APIRouter(prefix="/api/v1/profile", tags=["profile"])


@auth_router.post("/login", response_model=SessionResponse)
def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> SessionResponse:
    _require_origin(request, settings)
    user = db.scalar(select(AuthUser).where(AuthUser.username == payload.username))
    if not _verify_password(user, payload.password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_credentials")
    assert user is not None
    if _password_hasher.check_needs_rehash(user.password_hash):
        user.password_hash = _password_hasher.hash(payload.password)
    now = datetime.now(timezone.utc)
    session_token = secrets.token_urlsafe(32)
    csrf_token = secrets.token_urlsafe(32)
    stored = AuthSession(
        id=str(uuid4()),
        user_id=user.id,
        token_hash=_digest(session_token),
        csrf_hash=_digest(csrf_token),
        created_at=now,
        expires_at=now + timedelta(days=settings.auth_session_days),
    )
    db.add(stored)
    db.commit()
    _set_auth_cookies(response, settings, session_token, csrf_token)
    return SessionResponse(username=user.username, expires_at=stored.expires_at)


@auth_router.get("/session", response_model=SessionResponse)
def session(context: AuthContext = Depends(require_session)) -> SessionResponse:
    return SessionResponse(username=context.user.username, expires_at=context.session.expires_at)


@auth_router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    response: Response,
    context: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Response:
    context.session.revoked_at = datetime.now(timezone.utc)
    db.commit()
    _clear_auth_cookies(response, settings)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@profile_router.get("", response_model=ProfileResponse)
def get_profile(
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> ProfileResponse:
    return _profile_response(get_or_create_profile(db, settings))


@profile_router.patch("", response_model=ProfileResponse)
def patch_profile(
    payload: ProfilePatch,
    _context: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> ProfileResponse:
    profile = get_or_create_profile(db, settings)
    if "birth_date" in payload.model_fields_set:
        if payload.birth_date is not None and payload.birth_date >= date.today():
            raise HTTPException(status_code=422, detail="invalid_birth_date")
        profile.birth_date = payload.birth_date
    if "reference_sex" in payload.model_fields_set:
        profile.reference_sex = payload.reference_sex
    if payload.accept_ai_data_processing is not None:
        if payload.accept_ai_data_processing:
            profile.ai_data_consent_version = AI_DATA_CONSENT_VERSION
            profile.ai_data_consent_at = datetime.now(timezone.utc)
        else:
            profile.ai_data_consent_version = None
            profile.ai_data_consent_at = None
    db.commit()
    db.refresh(profile)
    from .ai_snapshot import enqueue_current_analysis

    enqueue_current_analysis(db, settings, trigger="manual", debounce_seconds=0)
    return _profile_response(profile)
