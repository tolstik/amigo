#!/usr/bin/env python3
"""Atomically replace the managed production checkpoint in Markdown files."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import tempfile


BEGIN = "<!-- BEGIN AMIGO PRODUCTION CHECKPOINT -->"
END = "<!-- END AMIGO PRODUCTION CHECKPOINT -->"
ALLOWED_TARGETS = {
    Path("/srv/amigo/AGENTS.md"),
    Path("/srv/amigo/docs/runbook.md"),
}


def update(target: Path, content: str) -> None:
    if target not in ALLOWED_TARGETS:
        raise ValueError(f"refusing to update unexpected path: {target}")
    original = target.read_text(encoding="utf-8")
    begin_count = original.count(BEGIN)
    end_count = original.count(END)
    managed = f"{BEGIN}\n{content.rstrip()}\n{END}"

    if begin_count == 0 and end_count == 0:
        updated = f"{original.rstrip()}\n\n## Latest production checkpoint\n\n{managed}\n"
    elif begin_count == 1 and end_count == 1:
        prefix, remainder = original.split(BEGIN, 1)
        _, suffix = remainder.split(END, 1)
        updated = f"{prefix}{managed}{suffix}"
    else:
        raise ValueError(f"invalid checkpoint markers in {target}")

    metadata = target.stat()
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.checkpoint-", dir=target.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(updated)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, metadata.st_mode & 0o777)
        os.chown(temporary, metadata.st_uid, metadata.st_gid)
        os.replace(temporary, target)
    finally:
        if temporary.exists():
            temporary.unlink()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("target", type=Path)
    parser.add_argument("checkpoint", type=Path)
    args = parser.parse_args()
    update(args.target, args.checkpoint.read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
