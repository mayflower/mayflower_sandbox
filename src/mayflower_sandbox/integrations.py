from __future__ import annotations

import json
import os
import re
import textwrap
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

import httpx
import yaml

from .filesystem import FileNotFoundError, VirtualFilesystem
from .mcp_bindings import MCPBindingManager

if TYPE_CHECKING:
    from collections.abc import Iterable

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)$", re.S)
_CODEBLOCK_RE = re.compile(r"```python\s*(.*?)```", re.S)

_mcp_manager = MCPBindingManager()


def _parse_skill_md(md: str) -> tuple[str, str]:
    name = "unnamed-skill"
    description = ""
    match = _FRONTMATTER_RE.match(md)
    if match:
        meta = yaml.safe_load(match.group(1)) or {}
        name = str(meta.get("name", name))
        description = str(meta.get("description", ""))
    return name, description


def _iter_py_blocks(md: str) -> Iterable[str]:
    for match in _CODEBLOCK_RE.finditer(md):
        yield match.group(1).strip() + "\n"


async def _fetch_skill_skillmd(source: str) -> str:
    if source.startswith("github:"):
        _, spec = source.split(":", 1)
        parts = spec.split("/")
        if len(parts) < 3:
            raise ValueError("github: source must be github:owner/repo/path[@branch]")
        owner, repo = parts[0], parts[1]
        branch = "main"
        if "@" in repo:
            repo, branch = repo.split("@", 1)
        remainder = parts[2:]
        if remainder:
            tail = remainder[-1]
            if "@" in tail:
                tail_path, branch = tail.split("@", 1)
                remainder[-1] = tail_path
        path = "/".join(remainder)
        url = f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{path.rstrip('/')}/SKILL.md"
    else:
        url = source

    async with httpx.AsyncClient(follow_redirects=True, timeout=30.0) as client:
        response = await client.get(url)
        response.raise_for_status()
        return response.text


def _sanitize_pkg_name(name: str) -> str:
    name = name.replace("-", "_")
    return re.sub(r"[^0-9a-zA-Z_]", "_", name)


async def _write_text(vfs: VirtualFilesystem, path: PurePosixPath, content: str) -> None:
    await vfs.write_file(str(path), content.encode("utf-8"), "text/plain")


async def _append_text(vfs: VirtualFilesystem, path: PurePosixPath, content: str) -> None:
    try:
        existing = await vfs.read_file(str(path))
        current = existing["content"].decode("utf-8")
    except FileNotFoundError:
        current = ""
    await vfs.write_file(str(path), (current + content).encode("utf-8"), "text/plain")


def _matches_allowlist(value: str, allowlist: list[str]) -> bool:
    lowered = value.lower()
    for token in allowlist:
        if lowered == token:
            return True
        if lowered.endswith(f".{token}"):
            return True
    return False


def _enforce_mcp_allowlist(name: str, url: str) -> None:
    raw_allow = os.environ.get("MAYFLOWER_MCP_ALLOWLIST")
    if raw_allow is None:
        return
    tokens = [token.strip().lower() for token in raw_allow.split(",") if token.strip()]
    if not tokens:
        raise PermissionError(
            "MAYFLOWER_MCP_ALLOWLIST is set but empty; no MCP servers may be bound."
        )
    host = urlparse(url).hostname or ""
    if _matches_allowlist(name, tokens):
        return
    if host and _matches_allowlist(host, tokens):
        return
    raise PermissionError(
        f"MCP server '{name}' ({host or 'unknown host'}) is not permitted by MAYFLOWER_MCP_ALLOWLIST."
    )


async def install_skill(
    db_pool,
    thread_id: str,
    source: str,
    *,
    compile_python: bool = True,
    into: str = "/site-packages/skills",
) -> dict[str, Any]:
    vfs = VirtualFilesystem(db_pool, thread_id)
    base = PurePosixPath(into)

    markdown = await _fetch_skill_skillmd(source)
    name, description = _parse_skill_md(markdown)
    safe_pkg = _sanitize_pkg_name(name)
    pkg_root = base / safe_pkg

    await _write_text(vfs, pkg_root / "SKILL.md", markdown)

    init_body = textwrap.dedent(
        """
        from pathlib import Path


        def instructions() -> str:
            return (Path(__file__).with_name("SKILL.md")).read_text(encoding="utf-8")
        """
    ).lstrip()
    await _write_text(vfs, pkg_root / "__init__.py", init_body)

    if compile_python:
        lib_root = pkg_root / "lib"
        wrote_any = False
        for idx, code in enumerate(_iter_py_blocks(markdown), start=1):
            wrote_any = True
            await _write_text(vfs, lib_root / f"snippet_{idx}.py", code)
        if wrote_any:
            await _append_text(
                vfs,
                pkg_root / "__init__.py",
                "\nfrom .lib import *  # auto-generated from SKILL.md code fences\n",
            )

    index_path = base / "index.json"
    try:
        raw_index = await vfs.read_file(str(index_path))
        index = json.loads(raw_index["content"].decode("utf-8"))
    except FileNotFoundError:
        index = {}
    index[name] = {"source": source, "description": description}
    await _write_text(vfs, index_path, json.dumps(index, indent=2))

    async with db_pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO sandbox_skills(thread_id, name, source, description)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (thread_id, name) DO UPDATE
            SET source = EXCLUDED.source, description = EXCLUDED.description
            """,
            thread_id,
            name,
            source,
            description,
        )

    return {
        "name": name,
        "package": f"skills.{safe_pkg}",
        "path": str(pkg_root),
        "description": description,
    }


def _snake(name: str) -> str:
    name = re.sub(r"[^0-9a-zA-Z]+", "_", name)
    name = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name)
    return name.strip("_").lower()


def _render_wrapper_module(server_name: str, tools: list[dict[str, Any]]) -> tuple[str, str]:
    exports: list[str] = []
    definitions: list[str] = []

    for tool in tools:
        fn_name = _snake(tool.get("name", "tool"))
        exports.append(fn_name)
        doc = (tool.get("description") or "").strip()
        schema = tool.get("inputSchema") or {}
        schema_excerpt = json.dumps(schema, default=str)[:1000]
        body = textwrap.dedent(
            f'''
            async def {fn_name}(**kwargs):
                """
                {doc}
                INPUT SCHEMA (abridged): {schema_excerpt}
                """
                from mayflower_mcp import call

                try:
                    from pyodide.ffi import to_py  # type: ignore
                    converted = to_py(kwargs, dict_converter=dict, list_converter=list)
                except (ImportError, AttributeError, TypeError):
                    converted = kwargs

                def _normalize(value):
                    if isinstance(value, dict):
                        return {{k: _normalize(v) for k, v in value.items()}}
                    if isinstance(value, (list, tuple)):
                        return [_normalize(v) for v in value]
                    return value

                payload = _normalize(converted)
                if not isinstance(payload, dict):
                    try:
                        payload = dict(payload)
                    except (TypeError, ValueError):
                        payload = {{"value": payload}}

                return await call("{server_name}", "{tool.get("name")}", payload)
            '''
        ).strip()
        definitions.append(body)

    if exports:
        exports_list = ", ".join(exports)
        all_list = ", ".join(f'"{name}"' for name in exports)
        init_py = f"from .tools import {exports_list}\n__all__ = [{all_list}]\n"
    else:
        init_py = "__all__ = []\n"

    tools_py = "\n\n".join(definitions) if definitions else "# No tools discovered yet.\n"
    return init_py, tools_py


async def add_http_mcp_server(
    db_pool,
    thread_id: str,
    name: str,
    url: str,
    headers: dict | None = None,
    auth: dict | None = None,
    *,
    discover: bool = True,
    into: str = "/site-packages/servers",
) -> dict[str, Any]:
    _enforce_mcp_allowlist(name, url)

    vfs = VirtualFilesystem(db_pool, thread_id)
    base = PurePosixPath(into)
    safe_pkg = _sanitize_pkg_name(name)
    pkg_root = base / safe_pkg

    headers_json = json.dumps(headers or {})
    auth_json = json.dumps(auth or {})

    async with db_pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO sandbox_mcp_servers(thread_id, name, url, headers, auth)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (thread_id, name) DO UPDATE
            SET url = EXCLUDED.url, headers = EXCLUDED.headers, auth = EXCLUDED.auth
            """,
            thread_id,
            name,
            url,
            headers_json,
            auth_json,
        )

    tool_specs: list[dict[str, Any]] = []
    if discover:
        tool_specs = await _mcp_manager.list_tools(thread_id, name, url=url, headers=headers)

    init_py, tools_py = _render_wrapper_module(name, tool_specs)
    await _write_text(vfs, pkg_root / "__init__.py", init_py)
    await _write_text(vfs, pkg_root / "tools.py", tools_py)
    await _write_text(
        vfs,
        pkg_root / "schemas.json",
        json.dumps({"tools": tool_specs}, indent=2),
    )

    return {
        "name": name,
        "package": f"servers.{safe_pkg}",
        "path": str(pkg_root),
        "url": url,
        "discover": discover,
    }
