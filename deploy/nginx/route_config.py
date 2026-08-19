#!/usr/bin/env python3
"""Add or remove the managed Amigo include in the two origin server blocks.

The utility writes only to stdout. Its caller is responsible for atomic
installation, `nginx -t`, and rollback. It intentionally targets the existing
tolstik.ru listeners on ports 80 and 443 instead of creating a competing
default vhost; the public TLS edge may preserve or rewrite the origin Host.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass


BEGIN = "# BEGIN AMIGO V2 ROUTE"
INCLUDE = "include /etc/nginx/snippets/amigo-v2-locations.conf;"
END = "# END AMIGO V2 ROUTE"
MARKER_RE = re.compile(
    rf"(?m)^[ \t]*{re.escape(BEGIN)}[ \t]*\n"
    rf"^[ \t]*{re.escape(INCLUDE)}[ \t]*\n"
    rf"^[ \t]*{re.escape(END)}[ \t]*(?:\n)?"
)
SERVER_RE = re.compile(r"(?m)^[ \t]*server[ \t]*\{")
LISTEN_RE = re.compile(r"(?m)^[ \t]*listen[ \t]+([^;]+);")
SERVER_NAME_RE = re.compile(r"(?m)^[ \t]*server_name[ \t]+([^;]+);")


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class ServerBlock:
    start: int
    close_brace: int
    port: int


def matching_brace(text: str, opening: int) -> int:
    depth = 0
    quote: str | None = None
    escaped = False
    in_comment = False

    for index in range(opening, len(text)):
        char = text[index]
        if in_comment:
            if char == "\n":
                in_comment = False
            continue
        if quote is not None:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            continue
        if char == "#":
            in_comment = True
        elif char in {'"', "'"}:
            quote = char
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index
            if depth < 0:
                break
    raise ConfigError("unbalanced braces in nginx configuration")


def target_servers(text: str) -> list[ServerBlock]:
    matches: list[ServerBlock] = []
    occupied_until = -1
    for server_match in SERVER_RE.finditer(text):
        if server_match.start() < occupied_until:
            continue
        opening = text.find("{", server_match.start(), server_match.end())
        closing = matching_brace(text, opening)
        occupied_until = closing + 1
        block = text[server_match.start() : closing + 1]

        names = {
            name
            for directive in SERVER_NAME_RE.findall(block)
            for name in directive.split()
        }
        if "tolstik.ru" not in names:
            continue

        ports: set[int] = set()
        for directive in LISTEN_RE.findall(block):
            first_token = directive.split()[0]
            port_text = first_token.rsplit(":", 1)[-1].strip("[]")
            if port_text.isdigit():
                ports.add(int(port_text))
        for port in sorted(ports & {80, 443}):
            matches.append(ServerBlock(server_match.start(), closing, port))

    found_ports = sorted(block.port for block in matches)
    if found_ports != [80, 443]:
        raise ConfigError(
            "expected exactly the tolstik.ru server blocks for ports 80 and 443; "
            f"found {found_ports}"
        )
    return matches


def enable(text: str) -> str:
    blocks = target_servers(text)
    existing = list(MARKER_RE.finditer(text))
    if existing:
        one_marker_per_block = all(
            sum(block.start < marker.start() < block.close_brace for marker in existing) == 1
            for block in blocks
        )
        if len(existing) != 2 or not one_marker_per_block:
            raise ConfigError("managed route markers exist in an unexpected location")
        return text

    insertion = (
        "\n    # BEGIN AMIGO V2 ROUTE\n"
        "    include /etc/nginx/snippets/amigo-v2-locations.conf;\n"
        "    # END AMIGO V2 ROUTE\n"
    )
    result = text
    for block in sorted(blocks, key=lambda item: item.close_brace, reverse=True):
        result = result[: block.close_brace] + insertion + result[block.close_brace :]
    return result


def disable(text: str) -> str:
    blocks = target_servers(text)
    existing = list(MARKER_RE.finditer(text))
    if not existing:
        return text
    one_marker_per_block = all(
        sum(block.start < marker.start() < block.close_brace for marker in existing) == 1
        for block in blocks
    )
    if len(existing) != 2 or not one_marker_per_block:
        raise ConfigError("managed route markers exist in an unexpected location")
    return MARKER_RE.sub("", text)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("enable", "disable"))
    args = parser.parse_args()
    source = sys.stdin.read()
    try:
        result = enable(source) if args.action == "enable" else disable(source)
    except ConfigError as error:
        print(f"route_config.py: {error}", file=sys.stderr)
        return 1
    sys.stdout.write(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
