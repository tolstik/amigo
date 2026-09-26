"""Read an explicitly staged private model HTML without following a symlink.

Only HTML bytes go to stdout, which deploy pipes directly to the web container.
Never run this script with stdout attached to a terminal or log.
"""
import os
import pwd
import stat
import sys


STAGING_PATH = "/home/tolstik/amigo-body-model.html"
MAX_HTML_BYTES = 2 * 1024 * 1024


def read_private_html(path: str, owner_uid: int) -> bytes:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != owner_uid
            or stat.S_IMODE(info.st_mode) != 0o600
            or not 0 < info.st_size <= MAX_HTML_BYTES
        ):
            raise ValueError("invalid private body model staging file")
        chunks = []
        remaining = MAX_HTML_BYTES + 1
        while remaining:
            chunk = os.read(fd, remaining)
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        content = b"".join(chunks)
        if len(content) != info.st_size or len(content) > MAX_HTML_BYTES:
            raise ValueError("private body model staging file changed or is too large")
        return content
    finally:
        os.close(fd)


def main() -> None:
    try:
        content = read_private_html(STAGING_PATH, pwd.getpwnam("tolstik").pw_uid)
    except (OSError, ValueError):
        raise SystemExit("private body model staging validation failed") from None
    sys.stdout.buffer.write(content)


if __name__ == "__main__":
    main()
