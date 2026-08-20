from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

from alembic import command
from alembic.config import Config
from cryptography.fernet import Fernet
from sqlalchemy import text

from .config import get_settings
from .db import SessionLocal
from .ai_snapshot import enqueue_current_analysis
from .legacy import import_legacy, import_legacy_weight_file
from .models import ProviderCredential
from .crypto import SecretCipher
from .service import ensure_default_plan
from .telegram import TelegramClient
from .withings import CredentialStore, WithingsClient


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
    commands.add_parser("bootstrap", help="migrate, seed the plan, and import OAuth tokens from secrets")
    commands.add_parser("telegram-test", help="send an explicitly marked non-health test message")
    commands.add_parser("ai-enqueue", help="enqueue an immediate minimized AI snapshot")
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
        print("bootstrap complete")
        return 0
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
