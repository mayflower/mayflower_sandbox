"""
Unit tests for SkillInstallTool and MCPBindHttpTool.

These tests verify the tool classes themselves, not just the underlying
integration functions (which are tested in test_skills_install.py and test_mcp_bind.py).
"""

import os
from unittest.mock import AsyncMock, MagicMock

import asyncpg
import pytest

from mayflower_sandbox.tools import MCPBindHttpTool, SkillInstallTool, create_sandbox_tools


@pytest.fixture
async def db_pool():
    """Create test database connection pool."""
    pool = await asyncpg.create_pool(
        host=os.getenv("POSTGRES_HOST", "localhost"),
        database=os.getenv("POSTGRES_DB", "mayflower_test"),
        user=os.getenv("POSTGRES_USER", "postgres"),
        password=os.getenv("POSTGRES_PASSWORD", "postgres"),
        port=int(os.getenv("POSTGRES_PORT", "5432")),
    )

    # Ensure test sessions exist
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO sandbox_sessions (thread_id, expires_at)
            VALUES
                ('test_skill_tool', NOW() + INTERVAL '1 day'),
                ('test_mcp_tool', NOW() + INTERVAL '1 day'),
                ('context_thread', NOW() + INTERVAL '1 day'),
                ('default', NOW() + INTERVAL '1 day')
            ON CONFLICT (thread_id) DO NOTHING
        """)

    yield pool
    await pool.close()


# =============================================================================
# SkillInstallTool Tests
# =============================================================================


class TestSkillInstallTool:
    """Tests for SkillInstallTool."""

    async def test_tool_has_correct_name_and_description(self, db_pool):
        """Test tool has correct metadata."""
        tool = SkillInstallTool(db_pool=db_pool, thread_id="test_skill_tool")

        assert tool.name == "skill_install"
        assert "Claude Skill" in tool.description
        assert "sandbox" in tool.description.lower()

    async def test_tool_has_correct_args_schema(self, db_pool):
        """Test tool has correct args schema."""
        tool = SkillInstallTool(db_pool=db_pool, thread_id="test_skill_tool")

        # Check args_schema has source field
        schema = tool.args_schema.model_json_schema()
        assert "source" in schema["properties"]
        assert "source" in schema["required"]

    async def test_tool_uses_instance_thread_id(self, db_pool, monkeypatch):
        """Test tool uses instance thread_id when no context provided."""
        tool = SkillInstallTool(db_pool=db_pool, thread_id="test_skill_tool")

        # Mock the underlying function
        mock_install = AsyncMock(
            return_value={"path": "/site-packages/skills/test", "name": "test"}
        )
        monkeypatch.setattr("mayflower_sandbox.tools_skills_mcp.install_skill", mock_install)

        await tool._arun(source="github:test/skill")

        # Verify install_skill was called with correct thread_id
        mock_install.assert_called_once()
        call_args = mock_install.call_args
        assert call_args[0][1] == "test_skill_tool"  # thread_id is second positional arg

    async def test_tool_uses_context_thread_id(self, db_pool, monkeypatch):
        """Test tool extracts thread_id from callback context."""
        tool = SkillInstallTool(db_pool=db_pool, thread_id=None)

        # Mock callback manager with thread_id in metadata
        mock_manager = MagicMock()
        mock_manager.metadata = {"configurable": {"thread_id": "context_thread"}}
        mock_manager.tags = []

        # Mock the underlying function
        mock_install = AsyncMock(
            return_value={"path": "/site-packages/skills/test", "name": "test"}
        )
        monkeypatch.setattr("mayflower_sandbox.tools_skills_mcp.install_skill", mock_install)

        await tool._arun(source="github:test/skill", run_manager=mock_manager)

        # Verify install_skill was called with context thread_id
        mock_install.assert_called_once()
        call_args = mock_install.call_args
        assert call_args[0][1] == "context_thread"

    async def test_tool_falls_back_to_default_thread_id(self, db_pool, monkeypatch):
        """Test tool falls back to 'default' when no thread_id available."""
        tool = SkillInstallTool(db_pool=db_pool, thread_id=None)

        # Mock the underlying function
        mock_install = AsyncMock(
            return_value={"path": "/site-packages/skills/test", "name": "test"}
        )
        monkeypatch.setattr("mayflower_sandbox.tools_skills_mcp.install_skill", mock_install)

        await tool._arun(source="github:test/skill")

        # Verify install_skill was called with default thread_id
        mock_install.assert_called_once()
        call_args = mock_install.call_args
        assert call_args[0][1] == "default"

    async def test_tool_passes_source_correctly(self, db_pool, monkeypatch):
        """Test tool passes source parameter to underlying function."""
        tool = SkillInstallTool(db_pool=db_pool, thread_id="test_skill_tool")

        mock_install = AsyncMock(return_value={"path": "/site-packages/skills/art", "name": "art"})
        monkeypatch.setattr("mayflower_sandbox.tools_skills_mcp.install_skill", mock_install)

        await tool._arun(source="github:anthropics/skills/algorithmic-art")

        # Verify source was passed correctly
        call_args = mock_install.call_args
        assert call_args[0][2] == "github:anthropics/skills/algorithmic-art"

    async def test_tool_returns_result_from_install_skill(self, db_pool, monkeypatch):
        """Test tool returns result from underlying function."""
        tool = SkillInstallTool(db_pool=db_pool, thread_id="test_skill_tool")

        expected_result = {
            "path": "/site-packages/skills/algorithmic_art",
            "name": "algorithmic_art",
            "instructions_available": True,
        }
        mock_install = AsyncMock(return_value=expected_result)
        monkeypatch.setattr("mayflower_sandbox.tools_skills_mcp.install_skill", mock_install)

        result = await tool._arun(source="github:anthropics/skills/algorithmic-art")

        assert result == expected_result

    async def test_tool_propagates_errors(self, db_pool, monkeypatch):
        """Test tool propagates errors from underlying function."""
        tool = SkillInstallTool(db_pool=db_pool, thread_id="test_skill_tool")

        mock_install = AsyncMock(side_effect=ValueError("Invalid skill source"))
        monkeypatch.setattr("mayflower_sandbox.tools_skills_mcp.install_skill", mock_install)

        with pytest.raises(ValueError, match="Invalid skill source"):
            await tool._arun(source="invalid:source")

    async def test_tool_in_factory_output(self, db_pool):
        """Test SkillInstallTool is included in factory output."""
        tools = create_sandbox_tools(db_pool, thread_id="test_skill_tool")

        skill_tool = next((t for t in tools if t.name == "skill_install"), None)
        assert skill_tool is not None
        assert isinstance(skill_tool, SkillInstallTool)


# =============================================================================
# MCPBindHttpTool Tests
# =============================================================================


class TestMCPBindHttpTool:
    """Tests for MCPBindHttpTool."""

    async def test_tool_has_correct_name_and_description(self, db_pool):
        """Test tool has correct metadata."""
        tool = MCPBindHttpTool(db_pool=db_pool, thread_id="test_mcp_tool")

        assert tool.name == "mcp_bind_http"
        assert "MCP" in tool.description
        assert "HTTP" in tool.description

    async def test_tool_has_correct_args_schema(self, db_pool):
        """Test tool has correct args schema."""
        tool = MCPBindHttpTool(db_pool=db_pool, thread_id="test_mcp_tool")

        schema = tool.args_schema.model_json_schema()
        assert "name" in schema["properties"]
        assert "url" in schema["properties"]
        assert "headers" in schema["properties"]
        assert "name" in schema["required"]
        assert "url" in schema["required"]
        # headers is optional
        assert "headers" not in schema.get("required", [])

    async def test_tool_uses_instance_thread_id(self, db_pool, monkeypatch):
        """Test tool uses instance thread_id when no context provided."""
        tool = MCPBindHttpTool(db_pool=db_pool, thread_id="test_mcp_tool")

        mock_bind = AsyncMock(return_value={"path": "/site-packages/servers/demo", "name": "demo"})
        monkeypatch.setattr("mayflower_sandbox.tools_skills_mcp.add_http_mcp_server", mock_bind)

        await tool._arun(name="demo", url="http://localhost:8000/mcp")

        mock_bind.assert_called_once()
        # thread_id is second positional arg
        assert mock_bind.call_args[0][1] == "test_mcp_tool"

    async def test_tool_uses_context_thread_id(self, db_pool, monkeypatch):
        """Test tool extracts thread_id from callback context."""
        tool = MCPBindHttpTool(db_pool=db_pool, thread_id=None)

        mock_manager = MagicMock()
        mock_manager.metadata = {"configurable": {"thread_id": "context_thread"}}
        mock_manager.tags = []

        mock_bind = AsyncMock(return_value={"path": "/site-packages/servers/demo", "name": "demo"})
        monkeypatch.setattr("mayflower_sandbox.tools_skills_mcp.add_http_mcp_server", mock_bind)

        await tool._arun(name="demo", url="http://localhost:8000/mcp", run_manager=mock_manager)

        mock_bind.assert_called_once()
        assert mock_bind.call_args[0][1] == "context_thread"

    async def test_tool_falls_back_to_default_thread_id(self, db_pool, monkeypatch):
        """Test tool falls back to 'default' when no thread_id available."""
        tool = MCPBindHttpTool(db_pool=db_pool, thread_id=None)

        mock_bind = AsyncMock(return_value={"path": "/site-packages/servers/demo", "name": "demo"})
        monkeypatch.setattr("mayflower_sandbox.tools_skills_mcp.add_http_mcp_server", mock_bind)

        await tool._arun(name="demo", url="http://localhost:8000/mcp")

        mock_bind.assert_called_once()
        assert mock_bind.call_args[0][1] == "default"

    async def test_tool_passes_name_and_url_correctly(self, db_pool, monkeypatch):
        """Test tool passes name and url parameters correctly."""
        tool = MCPBindHttpTool(db_pool=db_pool, thread_id="test_mcp_tool")

        mock_bind = AsyncMock(
            return_value={"path": "/site-packages/servers/myserver", "name": "myserver"}
        )
        monkeypatch.setattr("mayflower_sandbox.tools_skills_mcp.add_http_mcp_server", mock_bind)

        await tool._arun(name="myserver", url="http://api.example.com/mcp")

        call_kwargs = mock_bind.call_args[1]
        assert call_kwargs["name"] == "myserver"
        assert call_kwargs["url"] == "http://api.example.com/mcp"

    async def test_tool_passes_headers_when_provided(self, db_pool, monkeypatch):
        """Test tool passes headers when provided."""
        tool = MCPBindHttpTool(db_pool=db_pool, thread_id="test_mcp_tool")

        mock_bind = AsyncMock(return_value={"path": "/site-packages/servers/auth", "name": "auth"})
        monkeypatch.setattr("mayflower_sandbox.tools_skills_mcp.add_http_mcp_server", mock_bind)

        headers = {"Authorization": "Bearer token123", "X-API-Key": "key456"}
        await tool._arun(name="auth", url="http://secure.example.com/mcp", headers=headers)

        call_kwargs = mock_bind.call_args[1]
        assert call_kwargs["headers"] == headers

    async def test_tool_passes_none_headers_when_not_provided(self, db_pool, monkeypatch):
        """Test tool passes None for headers when not provided."""
        tool = MCPBindHttpTool(db_pool=db_pool, thread_id="test_mcp_tool")

        mock_bind = AsyncMock(return_value={"path": "/site-packages/servers/demo", "name": "demo"})
        monkeypatch.setattr("mayflower_sandbox.tools_skills_mcp.add_http_mcp_server", mock_bind)

        await tool._arun(name="demo", url="http://localhost:8000/mcp")

        call_kwargs = mock_bind.call_args[1]
        assert call_kwargs["headers"] is None

    async def test_tool_returns_result_from_add_http_mcp_server(self, db_pool, monkeypatch):
        """Test tool returns result from underlying function."""
        tool = MCPBindHttpTool(db_pool=db_pool, thread_id="test_mcp_tool")

        expected_result = {
            "path": "/site-packages/servers/demo",
            "name": "demo",
            "tools": ["echo", "ping"],
            "url": "http://localhost:8000/mcp",
        }
        mock_bind = AsyncMock(return_value=expected_result)
        monkeypatch.setattr("mayflower_sandbox.tools_skills_mcp.add_http_mcp_server", mock_bind)

        result = await tool._arun(name="demo", url="http://localhost:8000/mcp")

        assert result == expected_result

    async def test_tool_propagates_errors(self, db_pool, monkeypatch):
        """Test tool propagates errors from underlying function."""
        tool = MCPBindHttpTool(db_pool=db_pool, thread_id="test_mcp_tool")

        mock_bind = AsyncMock(side_effect=ConnectionError("Failed to connect to MCP server"))
        monkeypatch.setattr("mayflower_sandbox.tools_skills_mcp.add_http_mcp_server", mock_bind)

        with pytest.raises(ConnectionError, match="Failed to connect"):
            await tool._arun(name="broken", url="http://broken.example.com/mcp")

    async def test_tool_in_factory_output(self, db_pool):
        """Test MCPBindHttpTool is included in factory output."""
        tools = create_sandbox_tools(db_pool, thread_id="test_mcp_tool")

        mcp_tool = next((t for t in tools if t.name == "mcp_bind_http"), None)
        assert mcp_tool is not None
        assert isinstance(mcp_tool, MCPBindHttpTool)


# =============================================================================
# Thread ID Extraction Tests (shared behavior)
# =============================================================================


class TestThreadIdExtraction:
    """Test thread_id extraction from various sources."""

    async def test_extract_from_metadata_configurable(self, db_pool, monkeypatch):
        """Test extracting thread_id from metadata.configurable."""
        tool = SkillInstallTool(db_pool=db_pool, thread_id=None)

        mock_manager = MagicMock()
        mock_manager.metadata = {"configurable": {"thread_id": "from_metadata"}}
        mock_manager.tags = []

        mock_install = AsyncMock(return_value={})
        monkeypatch.setattr("mayflower_sandbox.tools_skills_mcp.install_skill", mock_install)

        await tool._arun(source="test", run_manager=mock_manager)
        assert mock_install.call_args[0][1] == "from_metadata"

    async def test_extract_from_tags(self, db_pool, monkeypatch):
        """Test extracting thread_id from tags."""
        tool = SkillInstallTool(db_pool=db_pool, thread_id=None)

        mock_manager = MagicMock()
        mock_manager.metadata = {}
        mock_manager.tags = ["other_tag", "thread_id:from_tags", "another_tag"]

        mock_install = AsyncMock(return_value={})
        monkeypatch.setattr("mayflower_sandbox.tools_skills_mcp.install_skill", mock_install)

        await tool._arun(source="test", run_manager=mock_manager)
        assert mock_install.call_args[0][1] == "from_tags"

    async def test_metadata_takes_priority_over_tags(self, db_pool, monkeypatch):
        """Test that metadata.configurable takes priority over tags."""
        tool = SkillInstallTool(db_pool=db_pool, thread_id=None)

        mock_manager = MagicMock()
        mock_manager.metadata = {"configurable": {"thread_id": "from_metadata"}}
        mock_manager.tags = ["thread_id:from_tags"]

        mock_install = AsyncMock(return_value={})
        monkeypatch.setattr("mayflower_sandbox.tools_skills_mcp.install_skill", mock_install)

        await tool._arun(source="test", run_manager=mock_manager)
        assert mock_install.call_args[0][1] == "from_metadata"

    async def test_instance_thread_id_used_when_no_context(self, db_pool, monkeypatch):
        """Test instance thread_id used when no callback context."""
        tool = SkillInstallTool(db_pool=db_pool, thread_id="instance_thread")

        mock_install = AsyncMock(return_value={})
        monkeypatch.setattr("mayflower_sandbox.tools_skills_mcp.install_skill", mock_install)

        # No run_manager provided
        await tool._arun(source="test")
        assert mock_install.call_args[0][1] == "instance_thread"

    async def test_context_overrides_instance_thread_id(self, db_pool, monkeypatch):
        """Test context thread_id overrides instance thread_id."""
        tool = SkillInstallTool(db_pool=db_pool, thread_id="instance_thread")

        mock_manager = MagicMock()
        mock_manager.metadata = {"configurable": {"thread_id": "context_thread"}}
        mock_manager.tags = []

        mock_install = AsyncMock(return_value={})
        monkeypatch.setattr("mayflower_sandbox.tools_skills_mcp.install_skill", mock_install)

        await tool._arun(source="test", run_manager=mock_manager)
        assert mock_install.call_args[0][1] == "context_thread"
