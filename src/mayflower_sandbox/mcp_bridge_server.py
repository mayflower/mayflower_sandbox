"""
Mayflower Sandbox - Persistent MCP Bridge Server.

Long-running HTTP bridge server for MCP calls from Pyodide workers.
Designed to be shared across all workers in a pool for better performance.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import asyncpg

from .mcp_bindings import MCPBindingManager

logger = logging.getLogger(__name__)


class MCPBridgeServer:
    """
    Long-running HTTP bridge for MCP calls from Pyodide workers.

    This server is started once and shared across all workers in a pool.
    It handles HTTP POST /call requests and routes them to the appropriate
    MCP server via the MCPBindingManager.
    """

    def __init__(self, db_pool: asyncpg.Pool, thread_id: str):
        """
        Initialize the MCP bridge server.

        Args:
            db_pool: PostgreSQL connection pool for loading MCP server configs
            thread_id: Thread ID for MCP session isolation
        """
        from .schema_validator import MCPSchemaValidator

        self.db_pool = db_pool
        self.thread_id = thread_id
        self._mcp_manager = MCPBindingManager()
        self._validator = MCPSchemaValidator()
        self._server: asyncio.AbstractServer | None = None
        self._port: int | None = None
        self._servers_cache: dict[str, dict[str, Any]] = {}

    @property
    def port(self) -> int | None:
        """Get the port the bridge is listening on."""
        return self._port

    @property
    def url(self) -> str | None:
        """Get the full URL of the bridge server."""
        return f"http://127.0.0.1:{self._port}" if self._port else None

    @property
    def is_running(self) -> bool:
        """Check if the bridge server is running."""
        return self._server is not None and self._server.is_serving()

    async def start(self) -> int:
        """
        Start the bridge server and return the port.

        Returns:
            The port number the server is listening on
        """
        if self.is_running:
            assert self._port is not None
            return self._port

        # Load MCP server configs from database
        self._servers_cache = await self._get_mcp_server_configs()

        # Start HTTP server on random port
        self._server = await asyncio.start_server(
            self._handle_request,
            host="127.0.0.1",
            port=0,
        )

        sock = self._server.sockets[0]
        assert sock is not None
        self._port = sock.getsockname()[1]

        logger.info(
            f"MCP bridge started on port {self._port} for thread {self.thread_id} "
            f"with {len(self._servers_cache)} registered servers"
        )
        return self._port

    async def reload_configs(self) -> None:
        """
        Reload MCP server configs from database.

        Call this when MCP servers are added/removed via mcp_bind_http tool.
        """
        self._servers_cache = await self._get_mcp_server_configs()
        logger.info(
            f"MCP bridge configs reloaded for thread {self.thread_id}: "
            f"{list(self._servers_cache.keys())}"
        )

    async def shutdown(self) -> None:
        """Shutdown the bridge server."""
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
            self._port = None
            logger.info(f"MCP bridge stopped for thread {self.thread_id}")

    async def _get_mcp_server_configs(self) -> dict[str, dict[str, Any]]:
        """Load MCP server configurations and schemas from database."""
        async with self.db_pool.acquire() as conn:
            try:
                rows = await conn.fetch(
                    """
                    SELECT name, url, headers, auth, schemas
                    FROM sandbox_mcp_servers
                    WHERE thread_id = $1
                    """,
                    self.thread_id,
                )
            except asyncpg.UndefinedTableError:
                rows = []
            except asyncpg.UndefinedColumnError:
                # Fall back if schemas column doesn't exist yet
                rows = await conn.fetch(
                    """
                    SELECT name, url, headers, auth, NULL as schemas
                    FROM sandbox_mcp_servers
                    WHERE thread_id = $1
                    """,
                    self.thread_id,
                )

        servers: dict[str, dict[str, Any]] = {}
        for row in rows:
            raw_headers = row["headers"] or {}
            if isinstance(raw_headers, str):
                raw_headers = json.loads(raw_headers)
            raw_auth = row["auth"] or {}
            if isinstance(raw_auth, str):
                raw_auth = json.loads(raw_auth)

            # Load schemas into validator
            raw_schemas = row["schemas"] or {}
            if isinstance(raw_schemas, str):
                raw_schemas = json.loads(raw_schemas)
            if raw_schemas:
                self._validator.load_schemas(row["name"], raw_schemas)

            servers[row["name"]] = {
                "url": row["url"],
                "headers": dict(raw_headers),
                "auth": dict(raw_auth),
            }
        return servers

    @staticmethod
    def _json_default(value: Any) -> Any:
        """JSON serializer for non-standard types."""
        if isinstance(value, str | int | float | bool) or value is None:
            return value
        if isinstance(value, list | tuple | set):
            return list(value)
        if isinstance(value, dict):
            return value
        return str(value)

    async def _execute_mcp_call(
        self, server_name: str, tool_name: str, args: dict
    ) -> tuple[str, bytes]:
        """Execute an MCP call and return (status, body)."""
        if server_name not in self._servers_cache:
            raise RuntimeError(f"MCP server '{server_name}' is not registered for this thread.")

        validation_errors = self._validator.validate(server_name, tool_name, args)
        if validation_errors:
            error_list = "; ".join(validation_errors)
            return (
                "400 Bad Request",
                json.dumps(
                    {"error": f"Validation failed for {server_name}.{tool_name}: {error_list}"}
                ).encode("utf-8"),
            )

        config = self._servers_cache[server_name]
        result = await self._mcp_manager.call(
            self.thread_id,
            server_name,
            tool_name,
            args,
            url=config["url"],
            headers=config.get("headers"),
        )
        return (
            "200 OK",
            json.dumps({"result": result}, default=self._json_default).encode("utf-8"),
        )

    async def _handle_request(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Handle HTTP request from Pyodide worker."""
        status = "200 OK"
        body = b"{}"
        try:
            # Parse HTTP request line
            request_line = await reader.readline()
            if not request_line:
                return
            parts = request_line.decode("ascii", errors="ignore").strip().split()
            if len(parts) < 3:
                raise ValueError("Malformed HTTP request line")
            method, path = parts[0], parts[1]

            # Parse headers
            headers: dict[str, str] = {}
            while True:
                line = await reader.readline()
                if not line or line == b"\r\n":
                    break
                key, _, value = line.decode("ascii", errors="ignore").partition(":")
                headers[key.strip().lower()] = value.strip()

            # Read body
            content_length = int(headers.get("content-length", "0"))
            payload = await reader.readexactly(content_length) if content_length > 0 else b""

            # Route request
            if method != "POST" or path != "/call":
                status = "404 Not Found"
                body = json.dumps({"error": "Endpoint not found"}).encode("utf-8")
            else:
                # Parse JSON payload and execute MCP call
                data = json.loads(payload.decode("utf-8"))
                status, body = await self._execute_mcp_call(
                    data.get("server"), data.get("tool"), data.get("args") or {}
                )

        except Exception as exc:  # noqa: BLE001 - return error payload to sandbox
            status = "500 Internal Server Error"
            body = json.dumps({"error": str(exc)}).encode("utf-8")

        finally:
            # Send HTTP response
            writer.write(
                (
                    f"HTTP/1.1 {status}\r\n"
                    "Content-Type: application/json\r\n"
                    f"Content-Length: {len(body)}\r\n"
                    "Connection: close\r\n"
                    "\r\n"
                ).encode()
            )
            writer.write(body)
            await writer.drain()
            writer.close()
            await writer.wait_closed()
