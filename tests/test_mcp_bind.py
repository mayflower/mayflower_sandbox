import os
import sys
from types import ModuleType, SimpleNamespace
from urllib.parse import urlparse

import asyncpg
import pytest

from mayflower_sandbox.filesystem import VirtualFilesystem
from mayflower_sandbox.integrations import add_http_mcp_server


@pytest.mark.asyncio
async def test_mcp_bind_creates_wrapper_and_calls_host(monkeypatch):
    db = await asyncpg.create_pool(
        database=os.environ.get("PGDATABASE", "mayflower_test"),
        user=os.environ.get("PGUSER", "postgres"),
        password=os.environ.get("PGPASSWORD", "postgres"),
        host=os.environ.get("PGHOST", "localhost"),
        port=int(os.environ.get("PGPORT", "5433")),
    )

    try:
        thread_id = "test_mcp_thread"
        server_name = "demo"
        server_url = "http://localhost:8000/mcp"

        host = urlparse(server_url).hostname or ""
        allow_value = ",".join(filter(None, [server_name, host]))
        monkeypatch.setenv("MAYFLOWER_MCP_ALLOWLIST", allow_value)
        monkeypatch.setenv("MAYFLOWER_MCP_CALL_INTERVAL", "0")

        async def fake_list_tools(thread_id_arg, name, *, url, headers=None):
            assert thread_id_arg == thread_id
            assert name == server_name
            return [
                {
                    "name": "echo",
                    "description": "Echo tool",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "message": {"type": "string", "description": "Message to echo"}
                        },
                        "required": ["message"],
                    },
                }
            ]

        monkeypatch.setattr(
            "mayflower_sandbox.integrations._mcp_manager.list_tools",
            fake_list_tools,
        )

        server_info = await add_http_mcp_server(
            db,
            thread_id,
            name=server_name,
            url=server_url,
            headers=None,
            discover=True,
        )

        calls: list[tuple[str, dict[str, str]]] = []

        async def stub_call(server, tool, args):
            calls.append((tool, args))
            return {"echoed": args}

        stub_module = ModuleType("mayflower_mcp")
        stub_module.call = stub_call
        monkeypatch.setitem(sys.modules, "mayflower_mcp", stub_module)

        ffi_module = ModuleType("pyodide.ffi")

        def to_py(value, **_kwargs):
            return value

        ffi_module.to_py = to_py
        pyodide_module = ModuleType("pyodide")
        pyodide_module.ffi = SimpleNamespace(to_py=to_py)
        monkeypatch.setitem(sys.modules, "pyodide", pyodide_module)
        monkeypatch.setitem(sys.modules, "pyodide.ffi", ffi_module)

        vfs = VirtualFilesystem(db, thread_id)
        tools_entry = await vfs.read_file(f"{server_info['path']}/tools.py")
        code = tools_entry["content"].decode("utf-8")

        module = ModuleType("servers.demo.tools")
        exec(code, module.__dict__)

        result = await module.echo(message="hello")
        assert result == {"echoed": {"message": "hello"}}
        assert calls == [("echo", {"message": "hello"})]
    finally:
        await db.close()
