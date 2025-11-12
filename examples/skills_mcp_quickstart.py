"""
Quickstart: Installing Claude Skills and binding MCP servers in Mayflower Sandbox.

Run this from the repo root after starting PostgreSQL via `make db-up` and
ensuring you have an HTTP MCP server reachable at STREAMABLE_HTTP_URL (ends with /mcp).
"""

from __future__ import annotations

import asyncio
import os
from urllib.parse import urlparse

import asyncpg

from mayflower_sandbox.integrations import add_http_mcp_server, install_skill
from mayflower_sandbox.sandbox_executor import SandboxExecutor


SKILL_SOURCE = os.environ.get(
    "MAYFLOWER_SKILL",
    "github:anthropics/skills/algorithmic-art",
)
MCP_SERVER_NAME = os.environ.get("MAYFLOWER_MCP_NAME", "demo")
MCP_SERVER_URL = os.environ.get("MAYFLOWER_MCP_URL", "http://localhost:8000/mcp")
THREAD_ID = os.environ.get("MAYFLOWER_THREAD_ID", "skills_mcp_demo")
DISCOVER = os.environ.get("MAYFLOWER_MCP_DISCOVER", "0").lower() in {"1", "true", "yes"}


async def main() -> None:
    db = await asyncpg.create_pool(
        database=os.environ.get("PGDATABASE", "mayflower_test"),
        user=os.environ.get("PGUSER", "postgres"),
        password=os.environ.get("PGPASSWORD", "postgres"),
        host=os.environ.get("PGHOST", "localhost"),
        port=int(os.environ.get("PGPORT", "5433")),
    )

    if os.environ.get("MAYFLOWER_MCP_ALLOWLIST") is None:
        host = urlparse(MCP_SERVER_URL).hostname or ""
        entries = [MCP_SERVER_NAME]
        if host:
            entries.append(host)
        os.environ["MAYFLOWER_MCP_ALLOWLIST"] = ",".join(entries)
        print(f"[demo] Setting MAYFLOWER_MCP_ALLOWLIST={os.environ['MAYFLOWER_MCP_ALLOWLIST']}")

    print(f"Installing skill from {SKILL_SOURCE}…")
    skill_info = await install_skill(db, THREAD_ID, SKILL_SOURCE)
    print("Skill installed:", skill_info)

    print(f"Binding MCP server {MCP_SERVER_NAME} at {MCP_SERVER_URL}…")
    server_info = await add_http_mcp_server(
        db,
        THREAD_ID,
        name=MCP_SERVER_NAME,
        url=MCP_SERVER_URL,
        headers=None,
        discover=DISCOVER,
    )
    print("MCP server bound:", server_info)

    skill_pkg = skill_info["package"]
    server_pkg = server_info["package"]
    executor = SandboxExecutor(db, THREAD_ID)
    code = """
from {skill_pkg} import instructions

try:
    from {server_pkg} import tools as server_tools
    TOOL_NAMES = [name for name in dir(server_tools) if not name.startswith("_")]
except ImportError:
    TOOL_NAMES = []

print(instructions()[:120])
print("Available MCP tools:", TOOL_NAMES)
""".format(
        skill_pkg=skill_pkg,
        server_pkg=f"{server_pkg}.tools",
    )
    print("Running sandbox code…")
    result = await executor.execute(code)
    print("Success:", result.success)
    print("stdout:\n", result.stdout)
    print("stderr:\n", result.stderr)

    await db.close()


if __name__ == "__main__":
    asyncio.run(main())
