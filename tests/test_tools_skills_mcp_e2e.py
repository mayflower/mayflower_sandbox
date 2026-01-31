"""
End-to-end integration tests for SkillInstallTool and MCPBindHttpTool.

These tests use real external services:
- GitHub (for skills): github:anthropics/skills/...
- Public MCP servers: DeepWiki (https://mcp.deepwiki.com/mcp)

Mark: @pytest.mark.external - requires network access to external services
Mark: @pytest.mark.slow - tests may take several seconds due to network latency

Run these tests explicitly:
    pytest tests/test_tools_skills_mcp_e2e.py -v

Skip in CI by excluding the 'external' marker:
    pytest -m "not external"
"""

import os

import asyncpg
import pytest

from mayflower_sandbox.filesystem import VirtualFilesystem
from mayflower_sandbox.integrations import _mcp_manager
from mayflower_sandbox.sandbox_executor import SandboxExecutor
from mayflower_sandbox.tools import MCPBindHttpTool, SkillInstallTool

# Mark all tests in this module as external and slow
pytestmark = [pytest.mark.external, pytest.mark.slow]


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
                ('e2e_skill_test', NOW() + INTERVAL '1 day'),
                ('e2e_mcp_test', NOW() + INTERVAL '1 day')
            ON CONFLICT (thread_id) DO NOTHING
        """)

    yield pool
    await pool.close()


@pytest.fixture
async def clean_skill_files(db_pool):
    """Clean skill-related files before each test."""
    async with db_pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM sandbox_filesystem WHERE thread_id = 'e2e_skill_test' AND file_path LIKE '/site-packages/skills/%'"
        )
        await conn.execute("DELETE FROM sandbox_skills WHERE thread_id = 'e2e_skill_test'")
    yield


@pytest.fixture
async def clean_mcp_files(db_pool):
    """Clean MCP-related files and session state before each test."""
    # Close any existing MCP sessions to prevent stale connections
    for _key, record in list(_mcp_manager._sessions.items()):
        try:
            await record.stack.aclose()
        except Exception:  # noqa: S110 - intentionally suppress cleanup errors
            pass
    _mcp_manager._sessions.clear()
    _mcp_manager._call_timestamps.clear()

    # Clean database
    async with db_pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM sandbox_filesystem WHERE thread_id = 'e2e_mcp_test' AND file_path LIKE '/site-packages/servers/%'"
        )
        await conn.execute("DELETE FROM sandbox_mcp_servers WHERE thread_id = 'e2e_mcp_test'")
    yield


# =============================================================================
# SkillInstallTool E2E Tests
# =============================================================================


class TestSkillInstallToolE2E:
    """End-to-end tests for SkillInstallTool with real GitHub skills."""

    async def test_install_algorithmic_art_skill(self, db_pool, clean_skill_files):
        """Test installing the algorithmic-art skill from Anthropic's public repo."""
        tool = SkillInstallTool(db_pool=db_pool, thread_id="e2e_skill_test")

        # Install the skill
        result = await tool._arun(source="github:anthropics/skills/skills/algorithmic-art")

        # Verify result structure
        assert "name" in result
        assert "path" in result
        assert "package" in result
        assert result["name"] == "algorithmic-art"
        assert result["package"] == "skills.algorithmic_art"

        # Verify files were created in VFS
        vfs = VirtualFilesystem(db_pool, "e2e_skill_test")
        skill_md = await vfs.read_file(f"{result['path']}/SKILL.md")
        assert skill_md["content"]
        assert b"algorithmic" in skill_md["content"].lower()

        init_py = await vfs.read_file(f"{result['path']}/__init__.py")
        assert init_py["content"]
        assert b"instructions" in init_py["content"]

    async def test_skill_importable_in_pyodide(self, db_pool, clean_skill_files):
        """Test that installed skill can be imported and used in Pyodide."""
        tool = SkillInstallTool(db_pool=db_pool, thread_id="e2e_skill_test")

        # Install the skill
        await tool._arun(source="github:anthropics/skills/skills/algorithmic-art")

        # Execute code in Pyodide that imports and uses the skill
        executor = SandboxExecutor(db_pool, "e2e_skill_test")
        code = """
import sys
sys.path.insert(0, '/site-packages')

from skills.algorithmic_art import instructions

# Get the instructions
instr = instructions()
print(f"Instructions length: {len(instr)}")
print(f"Contains 'art': {'art' in instr.lower()}")

# Show first 100 chars
print(f"Preview: {instr[:100]}")
"""
        result = await executor.execute(code)

        assert result.success, f"Execution failed: {result.stderr}"
        assert "Instructions length:" in result.stdout
        assert "Contains 'art': True" in result.stdout

    async def test_install_skill_creator_skill(self, db_pool, clean_skill_files):
        """Test installing the skill-creator meta skill."""
        tool = SkillInstallTool(db_pool=db_pool, thread_id="e2e_skill_test")

        install_result = await tool._arun(source="github:anthropics/skills/skills/skill-creator")

        assert install_result["name"] == "skill-creator"
        assert install_result["package"] == "skills.skill_creator"

        # Verify can import in Pyodide
        executor = SandboxExecutor(db_pool, "e2e_skill_test")
        code = """
import sys
sys.path.insert(0, '/site-packages')

from skills.skill_creator import instructions
instr = instructions()
print(f"Skill creator instructions: {len(instr)} chars")
print("SUCCESS" if len(instr) > 100 else "FAILED")
"""
        exec_result = await executor.execute(code)
        assert exec_result.success, f"Execution failed: {exec_result.stderr}"
        assert "SUCCESS" in exec_result.stdout

    async def test_install_multiple_skills(self, db_pool, clean_skill_files):
        """Test installing multiple skills in the same thread."""
        tool = SkillInstallTool(db_pool=db_pool, thread_id="e2e_skill_test")

        # Install two skills
        result1 = await tool._arun(source="github:anthropics/skills/skills/algorithmic-art")
        result2 = await tool._arun(source="github:anthropics/skills/skills/skill-creator")

        assert result1["name"] == "algorithmic-art"
        assert result2["name"] == "skill-creator"

        # Verify both are importable
        executor = SandboxExecutor(db_pool, "e2e_skill_test")
        code = """
import sys
sys.path.insert(0, '/site-packages')

from skills.algorithmic_art import instructions as art_instructions
from skills.skill_creator import instructions as creator_instructions

print(f"Art skill: {len(art_instructions())} chars")
print(f"Creator skill: {len(creator_instructions())} chars")
print("BOTH_IMPORTED")
"""
        result = await executor.execute(code)
        assert result.success, f"Execution failed: {result.stderr}"
        assert "BOTH_IMPORTED" in result.stdout

    async def test_skill_index_updated(self, db_pool, clean_skill_files):
        """Test that skills index.json is updated after installation."""
        tool = SkillInstallTool(db_pool=db_pool, thread_id="e2e_skill_test")

        await tool._arun(source="github:anthropics/skills/skills/algorithmic-art")

        # Check index.json
        vfs = VirtualFilesystem(db_pool, "e2e_skill_test")
        index_data = await vfs.read_file("/site-packages/skills/index.json")
        import json

        index = json.loads(index_data["content"].decode("utf-8"))

        assert "algorithmic-art" in index
        assert "source" in index["algorithmic-art"]
        assert "github:anthropics/skills" in index["algorithmic-art"]["source"]


# =============================================================================
# MCPBindHttpTool E2E Tests
# =============================================================================


class TestMCPBindHttpToolE2E:
    """End-to-end tests for MCPBindHttpTool with public MCP servers."""

    async def test_bind_deepwiki_server(self, db_pool, clean_mcp_files, monkeypatch):
        """Test binding DeepWiki public MCP server and discovering tools."""
        # Allow DeepWiki in allowlist
        monkeypatch.setenv("MAYFLOWER_MCP_ALLOWLIST", "deepwiki,mcp.deepwiki.com")
        monkeypatch.setenv("MAYFLOWER_MCP_CALL_INTERVAL", "0")

        tool = MCPBindHttpTool(db_pool=db_pool, thread_id="e2e_mcp_test")

        result = await tool._arun(
            name="deepwiki",
            url="https://mcp.deepwiki.com/mcp",
        )

        # Verify result structure
        assert "name" in result
        assert result["name"] == "deepwiki"
        assert "path" in result
        assert "url" in result
        assert result["url"] == "https://mcp.deepwiki.com/mcp"
        assert "discover" in result
        assert result["discover"] is True

        # Verify wrapper files were created
        vfs = VirtualFilesystem(db_pool, "e2e_mcp_test")
        files = await vfs.list_files(pattern="/site-packages/servers/deepwiki%")

        # Should have at least __init__.py and tools.py (or models.py for typed)
        file_paths = [f["file_path"] for f in files]
        assert any("__init__.py" in p for p in file_paths), f"Missing __init__.py in {file_paths}"

    async def test_deepwiki_wrapper_importable_in_pyodide(
        self, db_pool, clean_mcp_files, monkeypatch
    ):
        """Test that DeepWiki wrapper can be imported in Pyodide."""
        monkeypatch.setenv("MAYFLOWER_MCP_ALLOWLIST", "deepwiki,mcp.deepwiki.com")
        monkeypatch.setenv("MAYFLOWER_MCP_CALL_INTERVAL", "0")

        tool = MCPBindHttpTool(db_pool=db_pool, thread_id="e2e_mcp_test")
        await tool._arun(name="deepwiki", url="https://mcp.deepwiki.com/mcp")

        # Try to import the wrapper in Pyodide
        # Note: We can't actually call the tools because __MCP_CALL__ isn't injected
        # But we can verify the module structure is correct
        executor = SandboxExecutor(db_pool, "e2e_mcp_test")
        code = """
import sys
sys.path.insert(0, '/site-packages')

# Import the server package
try:
    import servers.deepwiki as deepwiki_pkg
    print(f"Package imported: {deepwiki_pkg}")
    print(f"Package __all__: {getattr(deepwiki_pkg, '__all__', 'N/A')}")

    # Check if tools module exists
    if hasattr(deepwiki_pkg, 'tools') or hasattr(deepwiki_pkg, '__all__'):
        print("IMPORT_SUCCESS")
    else:
        # For typed stubs, check for direct exports
        exports = [x for x in dir(deepwiki_pkg) if not x.startswith('_')]
        print(f"Exports: {exports[:5]}")
        print("IMPORT_SUCCESS")
except ImportError as e:
    print(f"Import failed: {e}")
    print("IMPORT_FAILED")
"""
        result = await executor.execute(code)

        assert result.success, f"Execution failed: {result.stderr}"
        assert "IMPORT_SUCCESS" in result.stdout, f"Import failed: {result.stdout}"

    async def test_bind_semgrep_server(self, db_pool, clean_mcp_files, monkeypatch):
        """Test binding Semgrep public MCP server."""
        monkeypatch.setenv("MAYFLOWER_MCP_ALLOWLIST", "semgrep,mcp.semgrep.ai")
        monkeypatch.setenv("MAYFLOWER_MCP_CALL_INTERVAL", "0")

        tool = MCPBindHttpTool(db_pool=db_pool, thread_id="e2e_mcp_test")

        result = await tool._arun(
            name="semgrep",
            url="https://mcp.semgrep.ai/mcp",
        )

        assert result["name"] == "semgrep"
        assert result["discover"] is True

        # Verify files were created
        vfs = VirtualFilesystem(db_pool, "e2e_mcp_test")
        files = await vfs.list_files(pattern="/site-packages/servers/semgrep%")
        assert len(files) > 0, "No wrapper files created"

    async def test_mcp_server_stored_in_database(self, db_pool, clean_mcp_files, monkeypatch):
        """Test that MCP server config is stored in database."""
        monkeypatch.setenv("MAYFLOWER_MCP_ALLOWLIST", "deepwiki,mcp.deepwiki.com")
        monkeypatch.setenv("MAYFLOWER_MCP_CALL_INTERVAL", "0")

        tool = MCPBindHttpTool(db_pool=db_pool, thread_id="e2e_mcp_test")
        await tool._arun(name="deepwiki", url="https://mcp.deepwiki.com/mcp")

        # Check database
        async with db_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM sandbox_mcp_servers WHERE thread_id = $1 AND name = $2",
                "e2e_mcp_test",
                "deepwiki",
            )

        assert row is not None
        assert row["url"] == "https://mcp.deepwiki.com/mcp"
        assert row["schemas"] is not None  # Should have discovered schemas

    async def test_mcp_allowlist_enforced(self, db_pool, clean_mcp_files, monkeypatch):
        """Test that MCP allowlist blocks unauthorized servers."""
        # Set restrictive allowlist
        monkeypatch.setenv("MAYFLOWER_MCP_ALLOWLIST", "allowed-only")

        tool = MCPBindHttpTool(db_pool=db_pool, thread_id="e2e_mcp_test")

        with pytest.raises(PermissionError, match="not permitted"):
            await tool._arun(name="blocked", url="https://blocked.example.com/mcp")

    async def test_wrapper_has_async_functions(self, db_pool, clean_mcp_files, monkeypatch):
        """Test that generated wrappers have async functions."""
        monkeypatch.setenv("MAYFLOWER_MCP_ALLOWLIST", "deepwiki,mcp.deepwiki.com")
        monkeypatch.setenv("MAYFLOWER_MCP_CALL_INTERVAL", "0")

        tool = MCPBindHttpTool(db_pool=db_pool, thread_id="e2e_mcp_test")
        await tool._arun(name="deepwiki", url="https://mcp.deepwiki.com/mcp")

        # Read the tools.py file and verify it has async functions
        vfs = VirtualFilesystem(db_pool, "e2e_mcp_test")

        # Try to find the tools file (could be tools.py or part of typed stubs)
        files = await vfs.list_files(pattern="/site-packages/servers/deepwiki%")
        file_paths = [f["file_path"] for f in files]

        # Find a Python file with tool definitions
        tools_file = None
        for path in file_paths:
            if path.endswith(".py") and "init" not in path:
                tools_file = path
                break

        assert tools_file is not None, f"No tools file found in {file_paths}"
        content = await vfs.read_file(tools_file)
        code = content["content"].decode("utf-8")
        assert "async def" in code, f"No async functions in {tools_file}"


# =============================================================================
# Combined E2E Tests
# =============================================================================


class TestCombinedE2E:
    """Tests that combine skills and MCP functionality."""

    async def test_skill_and_mcp_coexist(
        self, db_pool, clean_skill_files, clean_mcp_files, monkeypatch
    ):
        """Test that skills and MCP servers can coexist in the same thread."""
        # Use same thread for both
        thread_id = "e2e_skill_test"
        monkeypatch.setenv("MAYFLOWER_MCP_ALLOWLIST", "deepwiki,mcp.deepwiki.com")
        monkeypatch.setenv("MAYFLOWER_MCP_CALL_INTERVAL", "0")

        # Ensure MCP test also uses skill thread
        async with db_pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM sandbox_filesystem WHERE thread_id = $1 AND file_path LIKE '/site-packages/servers/%'",
                thread_id,
            )
            await conn.execute(
                "DELETE FROM sandbox_mcp_servers WHERE thread_id = $1",
                thread_id,
            )

        # Install skill
        skill_tool = SkillInstallTool(db_pool=db_pool, thread_id=thread_id)
        await skill_tool._arun(source="github:anthropics/skills/skills/algorithmic-art")

        # Bind MCP server
        mcp_tool = MCPBindHttpTool(db_pool=db_pool, thread_id=thread_id)
        await mcp_tool._arun(name="deepwiki", url="https://mcp.deepwiki.com/mcp")

        # Verify both are importable
        executor = SandboxExecutor(db_pool, thread_id)
        code = """
import sys
sys.path.insert(0, '/site-packages')

# Import skill
from skills.algorithmic_art import instructions
print(f"Skill loaded: {len(instructions())} chars")

# Import MCP server
import servers.deepwiki as deepwiki
print(f"MCP server loaded: {deepwiki}")

print("COEXIST_SUCCESS")
"""
        result = await executor.execute(code)

        assert result.success, f"Execution failed: {result.stderr}"
        assert "COEXIST_SUCCESS" in result.stdout
