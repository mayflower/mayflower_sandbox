"""Unit tests for MCPBindingManager — covers session management, throttling, and tool listing."""

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mayflower_sandbox.mcp_bindings import MCPBindingManager, _SessionRecord

# ---------------------------------------------------------------------------
# Constructor
# ---------------------------------------------------------------------------


class TestInit:
    def test_defaults(self):
        mgr = MCPBindingManager()
        assert mgr._session_ttl == 300.0
        assert mgr._min_call_interval == 0.1

    def test_env_overrides(self, monkeypatch):
        monkeypatch.setenv("MAYFLOWER_MCP_SESSION_TTL", "600")
        monkeypatch.setenv("MAYFLOWER_MCP_CALL_INTERVAL", "0.5")
        mgr = MCPBindingManager()
        assert mgr._session_ttl == 600.0
        assert mgr._min_call_interval == 0.5

    def test_ttl_minimum_60(self, monkeypatch):
        monkeypatch.setenv("MAYFLOWER_MCP_SESSION_TTL", "10")
        mgr = MCPBindingManager()
        assert mgr._session_ttl == 60.0

    def test_interval_minimum_0(self, monkeypatch):
        monkeypatch.setenv("MAYFLOWER_MCP_CALL_INTERVAL", "-1")
        mgr = MCPBindingManager()
        assert mgr._min_call_interval == 0.0


# ---------------------------------------------------------------------------
# _throttle
# ---------------------------------------------------------------------------


class TestThrottle:
    @pytest.mark.asyncio
    async def test_no_throttle_when_interval_zero(self, monkeypatch):
        monkeypatch.setenv("MAYFLOWER_MCP_CALL_INTERVAL", "0")
        mgr = MCPBindingManager()
        key = ("t1", "srv")
        start = time.monotonic()
        await mgr._throttle(key)
        await mgr._throttle(key)
        elapsed = time.monotonic() - start
        assert elapsed < 0.05  # should be near-instant

    @pytest.mark.asyncio
    async def test_throttle_waits(self, monkeypatch):
        monkeypatch.setenv("MAYFLOWER_MCP_CALL_INTERVAL", "0.1")
        mgr = MCPBindingManager()
        key = ("t1", "srv")
        await mgr._throttle(key)
        start = time.monotonic()
        await mgr._throttle(key)
        elapsed = time.monotonic() - start
        assert elapsed >= 0.05  # should have waited ~0.1s


# ---------------------------------------------------------------------------
# _close_session
# ---------------------------------------------------------------------------


class TestCloseSession:
    @pytest.mark.asyncio
    async def test_closes_and_removes(self):
        mgr = MCPBindingManager()
        key = ("t1", "srv")
        mock_stack = AsyncMock()
        record = _SessionRecord(
            stack=mock_stack,
            session=MagicMock(),
            expires_at=time.monotonic() + 300,
            last_used=time.monotonic(),
        )
        mgr._sessions[key] = record
        mgr._call_timestamps[key] = time.monotonic()

        await mgr._close_session(key, record)
        mock_stack.aclose.assert_called_once()
        assert key not in mgr._sessions
        assert key not in mgr._call_timestamps


# ---------------------------------------------------------------------------
# ensure_connected
# ---------------------------------------------------------------------------


class TestEnsureConnected:
    @pytest.mark.asyncio
    async def test_creates_new_session(self):
        mgr = MCPBindingManager()
        mock_session = AsyncMock()
        mock_stack = AsyncMock()

        with (
            patch("mayflower_sandbox.mcp_bindings.AsyncExitStack") as mock_stack_cls,
            patch("mayflower_sandbox.mcp_bindings.streamablehttp_client"),
            patch("mayflower_sandbox.mcp_bindings.ClientSession"),
        ):
            mock_stack_cls.return_value = mock_stack
            mock_stack.enter_async_context = AsyncMock(
                side_effect=[
                    (AsyncMock(), AsyncMock(), None),  # streamablehttp_client
                    mock_session,  # ClientSession
                ]
            )

            session = await mgr.ensure_connected("t1", "srv", "http://localhost:8000")
            assert session == mock_session
            mock_session.initialize.assert_called_once()

    @pytest.mark.asyncio
    async def test_reuses_existing_session(self):
        mgr = MCPBindingManager()
        mock_session = MagicMock()
        key = ("t1", "srv")
        mgr._sessions[key] = _SessionRecord(
            stack=AsyncMock(),
            session=mock_session,
            expires_at=time.monotonic() + 300,
            last_used=time.monotonic(),
        )

        session = await mgr.ensure_connected("t1", "srv", "http://localhost:8000")
        assert session == mock_session

    @pytest.mark.asyncio
    async def test_expired_session_recreated(self):
        mgr = MCPBindingManager()
        mock_old_session = MagicMock()
        key = ("t1", "srv")
        old_stack = AsyncMock()
        mgr._sessions[key] = _SessionRecord(
            stack=old_stack,
            session=mock_old_session,
            expires_at=time.monotonic() - 1,  # expired
            last_used=time.monotonic(),
        )

        mock_new_session = AsyncMock()
        with (
            patch("mayflower_sandbox.mcp_bindings.AsyncExitStack") as mock_stack_cls,
            patch("mayflower_sandbox.mcp_bindings.streamablehttp_client"),
            patch("mayflower_sandbox.mcp_bindings.ClientSession"),
        ):
            mock_stack_cls.return_value = AsyncMock()
            mock_stack_cls.return_value.enter_async_context = AsyncMock(
                side_effect=[
                    (AsyncMock(), AsyncMock(), None),
                    mock_new_session,
                ]
            )

            session = await mgr.ensure_connected("t1", "srv", "http://localhost:8000")
            assert session == mock_new_session
            old_stack.aclose.assert_called_once()


# ---------------------------------------------------------------------------
# call
# ---------------------------------------------------------------------------


class TestCall:
    @pytest.mark.asyncio
    async def test_delegates_to_session(self, monkeypatch):
        monkeypatch.setenv("MAYFLOWER_MCP_CALL_INTERVAL", "0")
        mgr = MCPBindingManager()
        mock_session = AsyncMock()
        mock_session.call_tool = AsyncMock(return_value={"content": [{"text": "ok"}]})

        key = ("t1", "srv")
        mgr._sessions[key] = _SessionRecord(
            stack=AsyncMock(),
            session=mock_session,
            expires_at=time.monotonic() + 300,
            last_used=time.monotonic(),
        )

        await mgr.call("t1", "srv", "echo", {"msg": "hi"}, url="http://x")
        mock_session.call_tool.assert_called_once_with("echo", {"msg": "hi"})


# ---------------------------------------------------------------------------
# list_tools
# ---------------------------------------------------------------------------


class TestListTools:
    @pytest.mark.asyncio
    async def test_returns_tool_list(self, monkeypatch):
        monkeypatch.setenv("MAYFLOWER_MCP_CALL_INTERVAL", "0")
        mgr = MCPBindingManager()

        mock_tool = MagicMock()
        mock_tool.name = "echo"
        mock_tool.description = "Echo tool"
        mock_tool.inputSchema = MagicMock()
        mock_tool.inputSchema.schema_ = {"type": "object"}

        mock_session = AsyncMock()
        mock_response = MagicMock()
        mock_response.tools = [mock_tool]
        mock_session.list_tools = AsyncMock(return_value=mock_response)

        key = ("t1", "srv")
        mgr._sessions[key] = _SessionRecord(
            stack=AsyncMock(),
            session=mock_session,
            expires_at=time.monotonic() + 300,
            last_used=time.monotonic(),
        )

        tools = await mgr.list_tools("t1", "srv", url="http://x")
        assert len(tools) == 1
        assert tools[0]["name"] == "echo"
        assert tools[0]["description"] == "Echo tool"
        assert tools[0]["inputSchema"] == {"type": "object"}

    @pytest.mark.asyncio
    async def test_fallback_to_schema_attr(self, monkeypatch):
        monkeypatch.setenv("MAYFLOWER_MCP_CALL_INTERVAL", "0")
        mgr = MCPBindingManager()

        mock_tool = MagicMock(spec=[])  # no attributes by default
        mock_tool.name = "tool"
        mock_tool.description = None
        # Only has .schema, not .schema_
        mock_input = MagicMock(spec=[])
        mock_input.schema = {"type": "object"}
        mock_tool.inputSchema = mock_input

        mock_session = AsyncMock()
        mock_response = MagicMock()
        mock_response.tools = [mock_tool]
        mock_session.list_tools = AsyncMock(return_value=mock_response)

        key = ("t1", "srv")
        mgr._sessions[key] = _SessionRecord(
            stack=AsyncMock(),
            session=mock_session,
            expires_at=time.monotonic() + 300,
            last_used=time.monotonic(),
        )

        tools = await mgr.list_tools("t1", "srv", url="http://x")
        assert tools[0]["inputSchema"] == {"type": "object"}
        assert tools[0]["description"] == ""
