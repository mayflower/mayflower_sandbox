# mypy: ignore-errors
"""
Tests for MCPBridgeServer - persistent HTTP bridge for MCP calls from workers.
"""

import asyncio
import json
import os

import asyncpg
import pytest

from mayflower_sandbox.mcp_bridge_server import MCPBridgeServer


@pytest.fixture
async def db_pool():
    """Create test database connection pool."""
    db_config = {
        "host": os.getenv("POSTGRES_HOST", "localhost"),
        "database": os.getenv("POSTGRES_DB", "mayflower_test"),
        "user": os.getenv("POSTGRES_USER", "postgres"),
        "password": os.getenv("POSTGRES_PASSWORD", "postgres"),
        "port": int(os.getenv("POSTGRES_PORT", "5432")),
    }

    pool = await asyncpg.create_pool(**db_config)

    # Ensure session exists
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO sandbox_sessions (thread_id, expires_at)
            VALUES ('test_mcp_bridge', NOW() + INTERVAL '1 day')
            ON CONFLICT (thread_id) DO NOTHING
        """
        )

    yield pool
    await pool.close()


@pytest.fixture
async def clean_mcp_servers(db_pool):
    """Clean MCP server configs before each test."""
    async with db_pool.acquire() as conn:
        try:
            await conn.execute(
                "DELETE FROM sandbox_mcp_servers WHERE thread_id = 'test_mcp_bridge'"
            )
        except asyncpg.UndefinedTableError:
            pass
    yield


async def test_bridge_starts_and_assigns_port(db_pool, clean_mcp_servers):
    """Test that bridge server starts and gets assigned a port."""
    bridge = MCPBridgeServer(db_pool, "test_mcp_bridge")

    try:
        port = await bridge.start()

        assert port > 0
        assert bridge.port == port
        assert bridge.url == f"http://127.0.0.1:{port}"
        assert bridge.is_running is True
    finally:
        await bridge.shutdown()

    assert bridge.is_running is False
    assert bridge.port is None


async def test_bridge_handles_not_found(db_pool, clean_mcp_servers):
    """Test that bridge returns 404 for unknown endpoints."""
    bridge = MCPBridgeServer(db_pool, "test_mcp_bridge")

    try:
        port = await bridge.start()

        # Make HTTP request to unknown endpoint
        reader, writer = await asyncio.open_connection("127.0.0.1", port)

        request = "GET /unknown HTTP/1.1\r\nHost: localhost\r\n\r\n"
        writer.write(request.encode())
        await writer.drain()

        response = await reader.read(4096)
        response_text = response.decode()

        assert "404 Not Found" in response_text

        writer.close()
        await writer.wait_closed()
    finally:
        await bridge.shutdown()


async def test_bridge_rejects_unregistered_server(db_pool, clean_mcp_servers):
    """Test that bridge rejects calls to unregistered MCP servers."""
    bridge = MCPBridgeServer(db_pool, "test_mcp_bridge")

    try:
        port = await bridge.start()

        # Make HTTP POST to /call with unregistered server
        reader, writer = await asyncio.open_connection("127.0.0.1", port)

        body = json.dumps({"server": "unknown_server", "tool": "test", "args": {}})
        request = (
            f"POST /call HTTP/1.1\r\n"
            f"Host: localhost\r\n"
            f"Content-Type: application/json\r\n"
            f"Content-Length: {len(body)}\r\n"
            f"\r\n"
            f"{body}"
        )
        writer.write(request.encode())
        await writer.drain()

        response = await reader.read(4096)
        response_text = response.decode()

        assert "500 Internal Server Error" in response_text
        assert "not registered" in response_text

        writer.close()
        await writer.wait_closed()
    finally:
        await bridge.shutdown()


async def test_bridge_reload_configs(db_pool, clean_mcp_servers):
    """Test that bridge can reload MCP server configs."""
    bridge = MCPBridgeServer(db_pool, "test_mcp_bridge")

    try:
        await bridge.start()

        # Initially no servers
        assert len(bridge._servers_cache) == 0

        # Add a server config to the database
        async with db_pool.acquire() as conn:
            try:
                await conn.execute(
                    """
                    INSERT INTO sandbox_mcp_servers (thread_id, name, url, headers, auth)
                    VALUES ('test_mcp_bridge', 'test_server', 'http://localhost:9999/mcp', '{}', '{}')
                """
                )
            except asyncpg.UndefinedTableError:
                pytest.skip("sandbox_mcp_servers table does not exist")

        # Reload configs
        await bridge.reload_configs()

        # Now should have one server
        assert "test_server" in bridge._servers_cache
        assert bridge._servers_cache["test_server"]["url"] == "http://localhost:9999/mcp"
    finally:
        await bridge.shutdown()


async def test_bridge_idempotent_start(db_pool, clean_mcp_servers):
    """Test that calling start() multiple times is idempotent."""
    bridge = MCPBridgeServer(db_pool, "test_mcp_bridge")

    try:
        port1 = await bridge.start()
        port2 = await bridge.start()

        assert port1 == port2
        assert bridge.is_running is True
    finally:
        await bridge.shutdown()


async def test_bridge_schema_validation_rejects_invalid_args(db_pool, clean_mcp_servers):
    """Test that bridge returns 400 when args fail schema validation."""
    bridge = MCPBridgeServer(db_pool, "test_mcp_bridge")

    try:
        # Add a server with schema to the database
        async with db_pool.acquire() as conn:
            try:
                # Schema requires 'title' as string
                schemas = {
                    "create_issue": {
                        "type": "object",
                        "properties": {
                            "title": {"type": "string"},
                            "body": {"type": "string"},
                        },
                        "required": ["title"],
                    }
                }
                await conn.execute(
                    """
                    INSERT INTO sandbox_mcp_servers (thread_id, name, url, headers, auth, schemas)
                    VALUES ('test_mcp_bridge', 'github', 'http://localhost:9999/mcp', '{}', '{}', $1)
                    """,
                    json.dumps(schemas),
                )
            except asyncpg.UndefinedTableError:
                pytest.skip("sandbox_mcp_servers table does not exist")

        port = await bridge.start()

        # Make HTTP POST with invalid args (missing required 'title')
        reader, writer = await asyncio.open_connection("127.0.0.1", port)

        body = json.dumps(
            {"server": "github", "tool": "create_issue", "args": {"body": "no title"}}
        )
        request = (
            f"POST /call HTTP/1.1\r\n"
            f"Host: localhost\r\n"
            f"Content-Type: application/json\r\n"
            f"Content-Length: {len(body)}\r\n"
            f"\r\n"
            f"{body}"
        )
        writer.write(request.encode())
        await writer.drain()

        response = await reader.read(4096)
        response_text = response.decode()

        assert "400 Bad Request" in response_text
        assert "Validation failed" in response_text
        assert "github.create_issue" in response_text

        writer.close()
        await writer.wait_closed()
    finally:
        await bridge.shutdown()


async def test_bridge_schema_validation_rejects_wrong_type(db_pool, clean_mcp_servers):
    """Test that bridge returns 400 when arg has wrong type."""
    bridge = MCPBridgeServer(db_pool, "test_mcp_bridge")

    try:
        async with db_pool.acquire() as conn:
            try:
                schemas = {
                    "list_issues": {
                        "type": "object",
                        "properties": {
                            "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                        },
                    }
                }
                await conn.execute(
                    """
                    INSERT INTO sandbox_mcp_servers (thread_id, name, url, headers, auth, schemas)
                    VALUES ('test_mcp_bridge', 'github', 'http://localhost:9999/mcp', '{}', '{}', $1)
                    """,
                    json.dumps(schemas),
                )
            except asyncpg.UndefinedTableError:
                pytest.skip("sandbox_mcp_servers table does not exist")

        port = await bridge.start()

        # Make HTTP POST with wrong type (string instead of integer)
        reader, writer = await asyncio.open_connection("127.0.0.1", port)

        body = json.dumps(
            {"server": "github", "tool": "list_issues", "args": {"limit": "not_an_int"}}
        )
        request = (
            f"POST /call HTTP/1.1\r\n"
            f"Host: localhost\r\n"
            f"Content-Type: application/json\r\n"
            f"Content-Length: {len(body)}\r\n"
            f"\r\n"
            f"{body}"
        )
        writer.write(request.encode())
        await writer.drain()

        response = await reader.read(4096)
        response_text = response.decode()

        assert "400 Bad Request" in response_text
        assert "Validation failed" in response_text

        writer.close()
        await writer.wait_closed()
    finally:
        await bridge.shutdown()


async def test_bridge_schema_validation_passes_valid_args(db_pool, clean_mcp_servers):
    """Test that valid args pass schema validation (will fail at MCP call since no real server)."""
    bridge = MCPBridgeServer(db_pool, "test_mcp_bridge")

    try:
        async with db_pool.acquire() as conn:
            try:
                schemas = {
                    "create_issue": {
                        "type": "object",
                        "properties": {
                            "title": {"type": "string"},
                        },
                        "required": ["title"],
                    }
                }
                await conn.execute(
                    """
                    INSERT INTO sandbox_mcp_servers (thread_id, name, url, headers, auth, schemas)
                    VALUES ('test_mcp_bridge', 'github', 'http://localhost:9999/mcp', '{}', '{}', $1)
                    """,
                    json.dumps(schemas),
                )
            except asyncpg.UndefinedTableError:
                pytest.skip("sandbox_mcp_servers table does not exist")

        port = await bridge.start()

        # Make HTTP POST with valid args
        reader, writer = await asyncio.open_connection("127.0.0.1", port)

        body = json.dumps(
            {"server": "github", "tool": "create_issue", "args": {"title": "Valid title"}}
        )
        request = (
            f"POST /call HTTP/1.1\r\n"
            f"Host: localhost\r\n"
            f"Content-Type: application/json\r\n"
            f"Content-Length: {len(body)}\r\n"
            f"\r\n"
            f"{body}"
        )
        writer.write(request.encode())
        await writer.drain()

        response = await reader.read(4096)
        response_text = response.decode()

        # Key assertion: Should NOT be 400 (validation passed)
        # May be 200 (MCP call succeeded or returned empty) or 500 (MCP server unreachable)
        assert "400 Bad Request" not in response_text
        assert "Validation failed" not in response_text

        writer.close()
        await writer.wait_closed()
    finally:
        await bridge.shutdown()


async def test_bridge_unknown_tool_passes_validation(db_pool, clean_mcp_servers):
    """Test that unknown tools pass validation (fail-open semantics)."""
    bridge = MCPBridgeServer(db_pool, "test_mcp_bridge")

    try:
        async with db_pool.acquire() as conn:
            try:
                # Only define schema for create_issue, not for unknown_tool
                schemas = {
                    "create_issue": {
                        "type": "object",
                        "properties": {"title": {"type": "string"}},
                        "required": ["title"],
                    }
                }
                await conn.execute(
                    """
                    INSERT INTO sandbox_mcp_servers (thread_id, name, url, headers, auth, schemas)
                    VALUES ('test_mcp_bridge', 'github', 'http://localhost:9999/mcp', '{}', '{}', $1)
                    """,
                    json.dumps(schemas),
                )
            except asyncpg.UndefinedTableError:
                pytest.skip("sandbox_mcp_servers table does not exist")

        port = await bridge.start()

        # Call unknown tool - should pass validation (fail-open)
        reader, writer = await asyncio.open_connection("127.0.0.1", port)

        body = json.dumps({"server": "github", "tool": "unknown_tool", "args": {"any": "args"}})
        request = (
            f"POST /call HTTP/1.1\r\n"
            f"Host: localhost\r\n"
            f"Content-Type: application/json\r\n"
            f"Content-Length: {len(body)}\r\n"
            f"\r\n"
            f"{body}"
        )
        writer.write(request.encode())
        await writer.drain()

        response = await reader.read(4096)
        response_text = response.decode()

        # Should NOT be 400 (validation passed with fail-open)
        assert "400 Bad Request" not in response_text

        writer.close()
        await writer.wait_closed()
    finally:
        await bridge.shutdown()
