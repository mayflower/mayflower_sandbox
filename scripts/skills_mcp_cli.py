#!/usr/bin/env python3
"""
Mayflower Skills & MCP management CLI.

Examples:
  mayflower skills install --source github:anthropics/skills/algorithmic-art --thread t1
  mayflower mcp add --name salesforce --url https://example.com/mcp --thread t1
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from typing import Any

import asyncpg

from mayflower_sandbox.integrations import add_http_mcp_server, install_skill


def _default_db_config() -> dict[str, Any]:
    return {
        "database": os.environ.get("PGDATABASE", "mayflower_test"),
        "user": os.environ.get("PGUSER", "postgres"),
        "password": os.environ.get("PGPASSWORD", "postgres"),
        "host": os.environ.get("PGHOST", "localhost"),
        "port": int(os.environ.get("PGPORT", "5433")),
    }


async def _install_skill(args: argparse.Namespace) -> None:
    db = await asyncpg.create_pool(**_default_db_config())
    try:
        info = await install_skill(db, args.thread, args.source)
        print("Skill installed:", info)
    finally:
        await db.close()


async def _add_mcp(args: argparse.Namespace) -> None:
    db = await asyncpg.create_pool(**_default_db_config())
    try:
        headers = {}
        if args.header:
            for header in args.header:
                key, _, value = header.partition(":")
                if not value:
                    raise ValueError(f"Invalid header '{header}'. Expected key:value.")
                headers[key.strip()] = value.strip()

        info = await add_http_mcp_server(
            db,
            args.thread,
            name=args.name,
            url=args.url,
            headers=headers or None,
            discover=not args.no_discover,
        )
        print("MCP server added:", info)
    finally:
        await db.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mayflower", description="Manage Mayflower skills and MCP servers."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    skills_parser = subparsers.add_parser("skills", help="Manage Claude skills.")
    skills_sub = skills_parser.add_subparsers(dest="skills_command", required=True)

    install_parser = skills_sub.add_parser("install", help="Install a skill into the sandbox VFS.")
    install_parser.add_argument(
        "--source", required=True, help="Skill source (e.g., github:owner/repo/path)"
    )
    install_parser.add_argument("--thread", required=True, help="Sandbox thread id")
    install_parser.set_defaults(func=_install_skill)

    mcp_parser = subparsers.add_parser("mcp", help="Manage MCP servers.")
    mcp_sub = mcp_parser.add_subparsers(dest="mcp_command", required=True)

    add_parser = mcp_sub.add_parser("add", help="Bind an HTTP MCP server.")
    add_parser.add_argument("--name", required=True, help="Server name (exposed as servers.<name>)")
    add_parser.add_argument(
        "--url", required=True, help="Streamable HTTP MCP endpoint (usually ends with /mcp)"
    )
    add_parser.add_argument("--thread", required=True, help="Sandbox thread id")
    add_parser.add_argument(
        "--header",
        action="append",
        help="Additional HTTP header key:value (can be specified multiple times)",
    )
    add_parser.add_argument(
        "--no-discover",
        action="store_true",
        help="Skip tool discovery after binding (wrappers will be empty)",
    )
    add_parser.set_defaults(func=_add_mcp)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        asyncio.run(args.func(args))
    except Exception as exc:  # noqa: BLE001
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
