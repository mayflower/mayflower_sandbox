from __future__ import annotations

import asyncio
import os
import time
from contextlib import AsyncExitStack
from dataclasses import dataclass
from typing import Any

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client


@dataclass
class _SessionRecord:
    stack: AsyncExitStack
    session: ClientSession
    expires_at: float
    last_used: float


class MCPBindingManager:
    """Host-side MCP session manager. Maintains persistent sessions per thread/server."""

    def __init__(self) -> None:
        self._sessions: dict[tuple[str, str], _SessionRecord] = {}
        self._call_timestamps: dict[tuple[str, str], float] = {}
        ttl_env = os.environ.get("MAYFLOWER_MCP_SESSION_TTL")
        interval_env = os.environ.get("MAYFLOWER_MCP_CALL_INTERVAL")
        self._session_ttl = max(60.0, float(ttl_env)) if ttl_env else 300.0
        self._min_call_interval = max(0.0, float(interval_env)) if interval_env else 0.1

    async def _close_session(self, key: tuple[str, str], record: _SessionRecord) -> None:
        try:
            await record.stack.aclose()
        finally:
            self._sessions.pop(key, None)
            self._call_timestamps.pop(key, None)

    async def _throttle(self, key: tuple[str, str]) -> None:
        if self._min_call_interval <= 0:
            return
        now = time.monotonic()
        last = self._call_timestamps.get(key)
        if last is not None:
            wait = self._min_call_interval - (now - last)
            if wait > 0:
                await asyncio.sleep(wait)
                now = time.monotonic()
        self._call_timestamps[key] = now

    async def ensure_connected(
        self,
        thread_id: str,
        name: str,
        url: str,
        headers: dict | None = None,
    ) -> ClientSession:
        key = (thread_id, name)
        now = time.monotonic()
        record = self._sessions.get(key)
        if record is not None and record.expires_at <= now:
            await self._close_session(key, record)
            record = None

        if record is None:
            stack = AsyncExitStack()
            read, write, _ = await stack.enter_async_context(
                streamablehttp_client(url, headers=headers)
            )
            session = await stack.enter_async_context(ClientSession(read, write))
            await session.initialize()
            record = _SessionRecord(
                stack=stack,
                session=session,
                expires_at=now + self._session_ttl,
                last_used=now,
            )
            self._sessions[key] = record
        else:
            record.expires_at = now + self._session_ttl
            record.last_used = now

        return record.session

    async def call(
        self,
        thread_id: str,
        name: str,
        tool: str,
        args: dict[str, Any] | None = None,
        *,
        url: str,
        headers: dict | None = None,
    ) -> Any:
        key = (thread_id, name)
        await self._throttle(key)
        session = await self.ensure_connected(thread_id, name, url, headers=headers)
        try:
            return await session.call_tool(tool, args or {})
        finally:
            record = self._sessions.get(key)
            if record:
                record.last_used = time.monotonic()

    async def list_tools(
        self,
        thread_id: str,
        name: str,
        *,
        url: str,
        headers: dict | None = None,
    ) -> list[dict[str, Any]]:
        key = (thread_id, name)
        await self._throttle(key)
        session = await self.ensure_connected(thread_id, name, url, headers=headers)
        response = await session.list_tools()
        record = self._sessions.get(key)
        if record:
            record.last_used = time.monotonic()
        tools = []
        for tool in response.tools:
            tools.append(
                {
                    "name": tool.name,
                    "description": tool.description or "",
                    "inputSchema": getattr(tool.inputSchema, "schema_", None)
                    or getattr(tool.inputSchema, "schema", None),
                }
            )
        return tools
