from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import getpass
import json
import os
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

from alembic import command
from alembic.config import Config
from cryptography.fernet import Fernet
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from .config import Settings, get_settings
from .db import SessionLocal
from .auth import PASSWORD_MIN_LENGTH, auth_is_configured, create_verification_session, set_password
from .ai_contracts import (
    AI_MODEL,
    AI_PROMPT_VERSION,
    MAX_ANALYSIS_REQUEST_ATTEMPT,
    snapshot_hash,
    validate_analysis_evidence,
)
from .ai_models import AiAnalysisJob
from .ai_queue import enqueue_analysis, latest_analysis
from .ai_snapshot import build_analysis_snapshot, enqueue_current_analysis
from .legacy import import_legacy, import_legacy_weight_file
from .labs import (
    backfill_stored_files,
    repair_lab_observed_dates,
    requeue_analyte_guide_regression_documents,
    requeue_extraction_timeout_documents,
)
from .models import ProviderCredential
from .crypto import SecretCipher
from .service import ensure_default_plan
from .telegram import TelegramClient
from .withings import CredentialStore, WithingsClient


def current_ai_analysis_ready(
    db: Session,
    settings: Settings,
    now: datetime | None = None,
) -> bool:
    """Check the exact current cache entry without printing snapshot data or hashes."""

    current = now or datetime.now(timezone.utc)
    snapshot = build_analysis_snapshot(
        db,
        settings.tz,
        current,
        user_height_cm=settings.user_height_cm,
    )
    state = latest_analysis(db, now=current)
    if (
        state.status != "ready"
        or state.analysis is None
        or state.snapshot_hash != snapshot_hash(snapshot)
        or state.model != AI_MODEL
        or state.prompt_version != AI_PROMPT_VERSION
    ):
        return False
    try:
        validate_analysis_evidence(state.analysis, snapshot)
    except ValueError:
        return False
    return True


def prepare_current_ai_retry(
    db: Session,
    settings: Settings,
    now: datetime | None = None,
) -> bool:
    """Prepare an exact current job after the dedicated worker has been stopped."""

    if not settings.ai_enabled:
        return False
    current = now or datetime.now(timezone.utc)
    snapshot = build_analysis_snapshot(
        db,
        settings.tz,
        current,
        user_height_cm=settings.user_height_cm,
    )
    digest = snapshot_hash(snapshot)
    processing = db.scalar(
        select(AiAnalysisJob)
        .where(
            AiAnalysisJob.snapshot_hash == digest,
            AiAnalysisJob.model == AI_MODEL,
            AiAnalysisJob.prompt_version == AI_PROMPT_VERSION,
            AiAnalysisJob.status == "processing",
        )
        .order_by(AiAnalysisJob.id.desc())
        .limit(1)
    )
    if processing is not None:
        processing.lease_until = None
        processing.last_error_code = "lease_expired"
        if processing.attempts >= min(
            settings.ai_max_attempts,
            MAX_ANALYSIS_REQUEST_ATTEMPT,
        ):
            processing.status = "failed"
            processing.finished_at = current
        else:
            processing.status = "pending"
            processing.available_at = current
        db.commit()
    enqueue_analysis(
        db,
        snapshot,
        trigger="manual",
        now=current,
        debounce_seconds=0,
        activity_min_interval_seconds=settings.ai_activity_min_interval_seconds,
        stale_seconds=settings.ai_stale_seconds,
        retry_terminal=True,
    )
    return True


def migrate() -> None:
    settings = get_settings()
    config_path = Path(__file__).resolve().parents[1] / "alembic.ini"
    config = Config(str(config_path))
    config.set_main_option("sqlalchemy.url", settings.database_url.replace("%", "%%"))
    command.upgrade(config, "head")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="amigo", description="Amigo v3 administrative CLI")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("migrate", help="apply all database migrations")
    commands.add_parser("health", help="check database connectivity")
    auth_password = commands.add_parser(
        "auth-set-password",
        help="create or rotate the single-user password and revoke existing sessions",
    )
    auth_password.add_argument(
        "--password-stdin",
        action="store_true",
        help="read exactly one password line from standard input",
    )
    commands.add_parser("auth-status", help="report whether local authentication is configured")
    verification = commands.add_parser(
        "auth-verification-session",
        help="write a short-lived root-only deployment verification session",
    )
    verification.add_argument("--directory", required=True)
    commands.add_parser("bootstrap", help="migrate, seed the plan, and import OAuth tokens from secrets")
    commands.add_parser("backfill-files", help="verify and copy legacy laboratory originals into PostgreSQL")
    commands.add_parser(
        "lab-retry-guide-regression",
        help="integrity-check and requeue only terminal TD-001 laboratory jobs",
    )
    commands.add_parser(
        "lab-retry-extraction-timeouts",
        help="integrity-check and requeue only terminal whole-page extraction timeouts",
    )
    commands.add_parser("telegram-test", help="send an explicitly marked non-health test message")
    commands.add_parser("ai-enqueue", help="enqueue an immediate minimized AI snapshot")
    commands.add_parser(
        "ai-ready",
        help="check for an exact current validated AI result without printing health data",
    )
    retry_ai = commands.add_parser(
        "ai-retry-current",
        help="prepare an exact current AI retry after the dedicated worker is stopped",
    )
    retry_ai.add_argument("--worker-stopped", action="store_true")
    handoff = commands.add_parser(
        "withings-token-handoff",
        help="write the current OAuth pair to a root-only rollback handoff mount",
    )
    handoff.add_argument("--directory", required=True)
    commands.add_parser("generate-encryption-key", help="generate a new Fernet key for a secret file")
    sync = commands.add_parser("sync", help="synchronize measurements from Withings")
    sync.add_argument("--full", action="store_true", help="page through the complete Withings history")
    sync.add_argument("--suppress-notifications", action="store_true")
    sync.add_argument("--reconcile-days", type=int)
    legacy = commands.add_parser("legacy-import", help="import missing records from the legacy database")
    legacy.add_argument("--url", required=True, help="SQLAlchemy URL for the legacy database")
    legacy.add_argument("--table")
    legacy.add_argument("--time-column")
    legacy.add_argument("--legacy-timezone", default="UTC")
    legacy_file = commands.add_parser(
        "legacy-weight-import", help="import headerless date_creat/weight TSV exported on the host"
    )
    legacy_file.add_argument("--file", required=True)
    legacy_file.add_argument("--timezone", default="UTC")
    legacy_file.add_argument("--scale", type=float, default=0.001)
    return parser


def execute(args: argparse.Namespace) -> int:
    settings = get_settings()
    if args.command in {"auth-set-password", "auth-status", "auth-verification-session"} and os.geteuid() != 0:
        print("authentication administration requires root", file=sys.stderr)
        return 77
    if args.command == "auth-status":
        with SessionLocal() as db:
            configured = auth_is_configured(db)
        print("configured" if configured else "not configured")
        return 0 if configured else 75
    if args.command == "auth-set-password":
        if args.password_stdin:
            password = sys.stdin.readline().rstrip("\r\n")
            if not password or sys.stdin.readline() != "":
                print("standard input must contain exactly one password line", file=sys.stderr)
                return 64
        else:
            if not sys.stdin.isatty():
                print("interactive TTY required; use --password-stdin for automation", file=sys.stderr)
                return 64
            password = getpass.getpass("New Amigo password: ")
            confirmation = getpass.getpass("Repeat Amigo password: ")
            if password != confirmation:
                print("passwords do not match", file=sys.stderr)
                return 65
        if len(password) < PASSWORD_MIN_LENGTH:
            print(f"password must contain at least {PASSWORD_MIN_LENGTH} characters", file=sys.stderr)
            return 65
        try:
            with SessionLocal() as db:
                set_password(db, settings.auth_username, password)
        finally:
            password = ""
        print("authentication password updated; existing sessions revoked")
        return 0
    if args.command == "auth-verification-session":
        directory = Path(args.directory)
        if directory.resolve() != Path("/verification") or not directory.is_dir():
            raise RuntimeError("verification directory must be the explicit /verification mount")
        target = directory / "session.json"
        with SessionLocal() as db:
            session_token, csrf_token, expires_at = create_verification_session(db, settings)
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o400)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(
                    {"session": session_token, "csrf": csrf_token, "expires_at": expires_at.isoformat()},
                    stream,
                    separators=(",", ":"),
                )
                stream.flush()
                os.fsync(stream.fileno())
        except Exception:
            target.unlink(missing_ok=True)
            raise
        print("verification session written")
        return 0
    if args.command == "generate-encryption-key":
        print(Fernet.generate_key().decode())
        return 0
    if args.command == "migrate":
        migrate()
        return 0
    if args.command == "health":
        try:
            with SessionLocal() as db:
                db.execute(text("SELECT 1"))
        except Exception:
            return 1
        print("ok")
        return 0
    if args.command == "bootstrap":
        migrate()
        with SessionLocal() as db:
            ensure_default_plan(db)
            CredentialStore(db, settings).bootstrap()
            db.commit()
            repaired_documents, repaired_reports, repaired_results = repair_lab_observed_dates(db)
        if repaired_documents:
            print(
                "laboratory dates repaired: "
                f"documents={repaired_documents}, reports={repaired_reports}, results={repaired_results}"
            )
        print("bootstrap complete")
        return 0
    if args.command == "backfill-files":
        with SessionLocal() as db:
            copied, missing = backfill_stored_files(db, settings.lab_storage_dir)
        if missing:
            print(f"file backfill incomplete: missing={missing}", file=sys.stderr)
            return 75
        print(f"file backfill complete: copied={copied}")
        return 0
    if args.command == "lab-retry-guide-regression":
        with SessionLocal() as db:
            eligible, requeued, skipped = requeue_analyte_guide_regression_documents(
                db,
                settings.lab_storage_dir,
            )
        print(
            "laboratory TD-001 retry complete: "
            f"eligible={eligible}, requeued={requeued}, skipped={skipped}"
        )
        return 75 if skipped else 0
    if args.command == "lab-retry-extraction-timeouts":
        with SessionLocal() as db:
            eligible, requeued, skipped = requeue_extraction_timeout_documents(
                db,
                settings.lab_storage_dir,
            )
        print(
            "laboratory extraction-timeout retry complete: "
            f"eligible={eligible}, requeued={requeued}, skipped={skipped}"
        )
        return 75 if skipped else 0
    if args.command == "telegram-test":
        client = TelegramClient(settings)
        try:
            client.send_message("<b>[Amigo v3 test]</b> Проверка доставки Telegram.")
        finally:
            client.close()
        print("test message sent")
        return 0
    if args.command == "ai-enqueue":
        with SessionLocal() as db:
            job = enqueue_current_analysis(
                db,
                settings,
                trigger="manual",
                debounce_seconds=0,
                retry_terminal=True,
            )
        if job is None:
            print("AI analysis is disabled")
        else:
            print(f"AI analysis job {job.id} queued")
        return 0
    if args.command == "ai-ready":
        with SessionLocal() as db:
            ready = current_ai_analysis_ready(db, settings)
        if ready:
            print("AI analysis ready")
            return 0
        print("AI analysis not ready", file=sys.stderr)
        return 75
    if args.command == "ai-retry-current":
        if not getattr(args, "worker_stopped", False):
            print("AI worker stop confirmation required", file=sys.stderr)
            return 64
        with SessionLocal() as db:
            prepared = prepare_current_ai_retry(db, settings)
        if not prepared:
            print("AI analysis disabled", file=sys.stderr)
            return 78
        print("AI analysis retry prepared")
        return 0
    if args.command == "withings-token-handoff":
        directory = Path(args.directory)
        if directory.resolve() != Path("/handoff") or not directory.is_dir():
            raise RuntimeError("token handoff directory must be the explicit /handoff mount")
        with SessionLocal() as db:
            credential = db.get(ProviderCredential, "withings")
            if credential is None:
                raise RuntimeError("Withings credentials are not initialized")
            cipher = SecretCipher(settings.token_encryption_key)
            values = {
                "access_token": cipher.decrypt(credential.access_token_encrypted),
                "refresh_token": cipher.decrypt(credential.refresh_token_encrypted),
            }
        for name, value in values.items():
            if not value or "\n" in value or "\r" in value or "\0" in value:
                raise RuntimeError("invalid OAuth token in credential store")
            target = directory / name
            descriptor = os.open(
                target,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o400,
            )
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                    stream.write(value)
                    stream.flush()
                    os.fsync(stream.fileno())
            except Exception:
                target.unlink(missing_ok=True)
                raise
        print("Withings rollback handoff files created")
        return 0
    if args.command == "sync":
        with SessionLocal() as db, WithingsClient(db, settings) as client:
            result = client.sync(
                full=args.full,
                suppress_notifications=True if args.suppress_notifications else None,
                reconcile_days=args.reconcile_days,
            )
        print(json.dumps(asdict(result), ensure_ascii=False, sort_keys=True))
        return 0
    if args.command == "legacy-import":
        with SessionLocal() as db:
            result = import_legacy(
                db,
                args.url,
                ZoneInfo(args.legacy_timezone),
                only_table=args.table,
                time_column=args.time_column,
            )
        print(json.dumps(asdict(result), ensure_ascii=False, sort_keys=True))
        return 0
    if args.command == "legacy-weight-import":
        with SessionLocal() as db:
            result = import_legacy_weight_file(
                db,
                args.file,
                ZoneInfo(args.timezone),
                scale=args.scale,
            )
        print(json.dumps(asdict(result), ensure_ascii=False, sort_keys=True))
        return 0
    return 2


def main() -> None:
    try:
        raise SystemExit(execute(build_parser().parse_args()))
    except KeyboardInterrupt:
        raise SystemExit(130) from None


if __name__ == "__main__":
    main()
