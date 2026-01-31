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
        database=os.environ.get("POSTGRES_DB", "mayflower_test"),
        user=os.environ.get("POSTGRES_USER", "postgres"),
        password=os.environ.get("POSTGRES_PASSWORD", "postgres"),
        host=os.environ.get("POSTGRES_HOST", "localhost"),
        port=int(os.environ.get("POSTGRES_PORT", "5432")),
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

        # Read both generated modules
        tools_entry = await vfs.read_file(f"{server_info['path']}/tools.py")
        tools_code = tools_entry["content"].decode("utf-8")

        models_entry = await vfs.read_file(f"{server_info['path']}/models.py")
        models_code = models_entry["content"].decode("utf-8")

        # Set up the module hierarchy for relative imports to work
        # Create servers package
        servers_pkg = ModuleType("servers")
        servers_pkg.__path__ = []
        monkeypatch.setitem(sys.modules, "servers", servers_pkg)

        # Create servers.demo package
        demo_pkg = ModuleType("servers.demo")
        demo_pkg.__path__ = []
        monkeypatch.setitem(sys.modules, "servers.demo", demo_pkg)

        # Create and execute models module
        models_module = ModuleType("servers.demo.models")
        models_module.__package__ = "servers.demo"
        exec(models_code, models_module.__dict__)
        monkeypatch.setitem(sys.modules, "servers.demo.models", models_module)

        # Create and execute tools module
        tools_module = ModuleType("servers.demo.tools")
        tools_module.__package__ = "servers.demo"
        exec(tools_code, tools_module.__dict__)
        monkeypatch.setitem(sys.modules, "servers.demo.tools", tools_module)

        result = await tools_module.echo(message="hello")
        assert result == {"echoed": {"message": "hello"}}
        assert calls == [("echo", {"message": "hello"})]
    finally:
        await db.close()
