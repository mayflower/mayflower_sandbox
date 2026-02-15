"""Unit tests for integrations.py — covers parsing, allowlist, wrappers, and edge cases."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mayflower_sandbox.integrations import (
    _enforce_mcp_allowlist,
    _fetch_skill_skillmd,
    _iter_py_blocks,
    _matches_allowlist,
    _parse_skill_md,
    _render_wrapper_module,
    _sanitize_pkg_name,
    _snake,
    add_http_mcp_server,
)

# ---------------------------------------------------------------------------
# _parse_skill_md
# ---------------------------------------------------------------------------


class TestParseSkillMd:
    def test_with_frontmatter(self):
        md = "---\nname: my-skill\ndescription: Does stuff\n---\n# Body"
        name, desc = _parse_skill_md(md)
        assert name == "my-skill"
        assert desc == "Does stuff"

    def test_without_frontmatter(self):
        md = "# Just markdown\nNo frontmatter here."
        name, desc = _parse_skill_md(md)
        assert name == "unnamed-skill"
        assert desc == ""

    def test_empty_frontmatter(self):
        md = "---\n---\n# Empty"
        name, desc = _parse_skill_md(md)
        assert name == "unnamed-skill"
        assert desc == ""


# ---------------------------------------------------------------------------
# _iter_py_blocks
# ---------------------------------------------------------------------------


class TestIterPyBlocks:
    def test_extracts_python_blocks(self):
        md = "text\n```python\nprint('hello')\n```\nmore\n```python\nx = 1\n```\n"
        blocks = list(_iter_py_blocks(md))
        assert len(blocks) == 2
        assert "print('hello')" in blocks[0]
        assert "x = 1" in blocks[1]

    def test_no_python_blocks(self):
        md = "just text, no code"
        blocks = list(_iter_py_blocks(md))
        assert blocks == []

    def test_non_python_blocks_ignored(self):
        md = "```javascript\nconsole.log('hi')\n```\n"
        blocks = list(_iter_py_blocks(md))
        assert blocks == []


# ---------------------------------------------------------------------------
# _sanitize_pkg_name / _snake
# ---------------------------------------------------------------------------


class TestNaming:
    def test_sanitize_hyphens(self):
        assert _sanitize_pkg_name("my-skill") == "my_skill"

    def test_sanitize_special_chars(self):
        assert _sanitize_pkg_name("skill@2.0") == "skill_2_0"

    def test_snake_camel(self):
        assert _snake("myFunction") == "my_function"

    def test_snake_special(self):
        assert _snake("some-tool.name") == "some_tool_name"

    def test_snake_strips_leading_trailing(self):
        assert _snake("__test__") == "test"


# ---------------------------------------------------------------------------
# _matches_allowlist
# ---------------------------------------------------------------------------


class TestMatchesAllowlist:
    def test_exact_match(self):
        assert _matches_allowlist("github", ["github", "notion"]) is True

    def test_suffix_match(self):
        assert _matches_allowlist("api.github.com", ["github.com"]) is True

    def test_no_match(self):
        assert _matches_allowlist("evil.com", ["github.com"]) is False

    def test_case_insensitive(self):
        assert _matches_allowlist("GitHub", ["github"]) is True


# ---------------------------------------------------------------------------
# _enforce_mcp_allowlist
# ---------------------------------------------------------------------------


class TestEnforceMcpAllowlist:
    def test_no_allowlist_env(self, monkeypatch):
        monkeypatch.delenv("MAYFLOWER_MCP_ALLOWLIST", raising=False)
        _enforce_mcp_allowlist("any", "http://any.com")  # should not raise

    def test_empty_allowlist_raises(self, monkeypatch):
        monkeypatch.setenv("MAYFLOWER_MCP_ALLOWLIST", "")
        with pytest.raises(PermissionError, match="empty"):
            _enforce_mcp_allowlist("github", "http://github.com")

    def test_name_match_passes(self, monkeypatch):
        monkeypatch.setenv("MAYFLOWER_MCP_ALLOWLIST", "github,notion")
        _enforce_mcp_allowlist("github", "http://some-url.com")

    def test_host_match_passes(self, monkeypatch):
        monkeypatch.setenv("MAYFLOWER_MCP_ALLOWLIST", "example.com")
        _enforce_mcp_allowlist("myserver", "http://example.com/mcp")

    def test_no_match_raises(self, monkeypatch):
        monkeypatch.setenv("MAYFLOWER_MCP_ALLOWLIST", "github.com")
        with pytest.raises(PermissionError, match="not permitted"):
            _enforce_mcp_allowlist("evil", "http://evil.com/mcp")


# ---------------------------------------------------------------------------
# _render_wrapper_module
# ---------------------------------------------------------------------------


class TestRenderWrapperModule:
    def test_empty_tools(self):
        init_py, tools_py = _render_wrapper_module("srv", [])
        assert "__all__ = []" in init_py
        assert "No tools" in tools_py

    def test_single_tool(self):
        tools = [{"name": "echo", "description": "Echo tool", "inputSchema": {}}]
        init_py, tools_py = _render_wrapper_module("srv", tools)
        assert "echo" in init_py
        assert "async def echo" in tools_py
        assert "mayflower_mcp" in tools_py

    def test_multiple_tools(self):
        tools = [
            {"name": "tool-one", "description": "First"},
            {"name": "tool_two", "description": "Second"},
        ]
        init_py, tools_py = _render_wrapper_module("srv", tools)
        assert "tool_one" in init_py
        assert "tool_two" in init_py


# ---------------------------------------------------------------------------
# _fetch_skill_skillmd
# ---------------------------------------------------------------------------


class TestFetchSkillMd:
    @pytest.mark.asyncio
    async def test_github_source_url_construction(self):
        with patch("mayflower_sandbox.integrations.httpx.AsyncClient") as mock_cls:
            mock_resp = MagicMock()
            mock_resp.text = "# SKILL.md content"
            mock_resp.raise_for_status = MagicMock()

            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_cls.return_value = mock_client

            result = await _fetch_skill_skillmd("github:owner/repo/path/to/skill")
            assert result == "# SKILL.md content"
            url_called = mock_client.get.call_args[0][0]
            assert "raw.githubusercontent.com/owner/repo/main/path/to/skill/SKILL.md" in url_called

    @pytest.mark.asyncio
    async def test_github_source_with_branch(self):
        with patch("mayflower_sandbox.integrations.httpx.AsyncClient") as mock_cls:
            mock_resp = MagicMock()
            mock_resp.text = "content"
            mock_resp.raise_for_status = MagicMock()

            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_cls.return_value = mock_client

            await _fetch_skill_skillmd("github:owner/repo@dev/path")
            url_called = mock_client.get.call_args[0][0]
            assert "/dev/" in url_called

    @pytest.mark.asyncio
    async def test_github_too_few_parts_raises(self):
        with pytest.raises(ValueError, match="github:"):
            await _fetch_skill_skillmd("github:owner/repo")

    @pytest.mark.asyncio
    async def test_plain_url(self):
        with patch("mayflower_sandbox.integrations.httpx.AsyncClient") as mock_cls:
            mock_resp = MagicMock()
            mock_resp.text = "direct content"
            mock_resp.raise_for_status = MagicMock()

            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_cls.return_value = mock_client

            result = await _fetch_skill_skillmd("https://example.com/SKILL.md")
            assert result == "direct content"


# ---------------------------------------------------------------------------
# add_http_mcp_server — typed fallback path
# ---------------------------------------------------------------------------


class TestAddHttpMcpServer:
    @pytest.mark.asyncio
    async def test_fallback_to_kwargs_wrappers_on_codegen_error(self, monkeypatch):
        """When typed codegen fails, should fall back to kwargs-based wrappers."""
        monkeypatch.delenv("MAYFLOWER_MCP_ALLOWLIST", raising=False)

        mock_pool = MagicMock()
        mock_conn = AsyncMock()
        mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

        mock_vfs = AsyncMock()
        monkeypatch.setattr(
            "mayflower_sandbox.integrations.VirtualFilesystem", lambda pool, tid: mock_vfs
        )

        tools = [{"name": "test_tool", "description": "A tool", "inputSchema": {"type": "object"}}]
        monkeypatch.setattr(
            "mayflower_sandbox.integrations._mcp_manager.list_tools",
            AsyncMock(return_value=tools),
        )

        # Make codegen raise to trigger fallback
        monkeypatch.setattr(
            "mayflower_sandbox.schema_codegen.generate_server_package",
            MagicMock(side_effect=RuntimeError("codegen failed")),
        )

        result = await add_http_mcp_server(
            mock_pool, "t1", "srv", "http://localhost:8000/mcp", discover=True, typed=True
        )

        assert result["typed"] is False
        # Verify kwargs-based files were written
        write_calls = [c[0][0] for c in mock_vfs.write_file.call_args_list]
        written_paths = [str(p) for p in write_calls]
        assert any("tools.py" in p for p in written_paths)
        assert any("schemas.json" in p for p in written_paths)
