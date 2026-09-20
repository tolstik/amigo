"""Read an explicitly staged operator-owned image without following a symlink.

Only image bytes go to stdout, which deploy pipes directly to the web container.
This script must never be run with its stdout attached to a terminal or log.
"""
import os
import pwd
import stat
import sys

path = "/home/tolstik/amigo-body-face.png"
limit = 512 * 1024
fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
with os.fdopen(fd, "rb") as source:
    info = os.fstat(source.fileno())
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != pwd.getpwnam("tolstik").pw_uid
        or stat.S_IMODE(info.st_mode) != 0o600
        or not 0 < info.st_size <= limit
    ):
        raise SystemExit("invalid private body texture staging file")
    content = source.read(limit + 1)
    if len(content) > limit:
        raise SystemExit("private body texture is too large")
    sys.stdout.buffer.write(content)
