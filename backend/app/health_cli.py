from __future__ import annotations

import argparse
import json

from .db import SessionLocal
from .health_ingest import HealthIngestError, approve_device, pending_devices


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Amigo Health Connect administration")
    commands = parser.add_subparsers(dest="command", required=True)
    approve = commands.add_parser("approve-device", help="approve a pending pairing code")
    approve.add_argument("pairing_code")
    commands.add_parser("list-pending", help="list pending devices without key material")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        with SessionLocal() as db:
            if args.command == "approve-device":
                device = approve_device(db, args.pairing_code)
                print(json.dumps({"device_id": device.id, "status": device.status}))
            else:
                print(json.dumps(pending_devices(db), ensure_ascii=False))
    except HealthIngestError as exc:
        print(json.dumps({"error": exc.code}))
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
