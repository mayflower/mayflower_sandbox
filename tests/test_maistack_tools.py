"""
Tests for maistack_tools bootstrap module - convenience wrappers for MAI Stack tools.

These tests verify that the maistack_tools.py module is correctly written to VFS
and that the code structure is valid for execution in Pyodide.
"""

import ast
import os

import asyncpg
import pytest

from mayflower_sandbox.bootstrap import MAISTACK_TOOLS_SHIM, write_bootstrap_files
from mayflower_sandbox.filesystem import VirtualFilesystem


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
            VALUES ('test_maistack_tools', NOW() + INTERVAL '1 day')
            ON CONFLICT (thread_id) DO NOTHING
        """
        )

    yield pool
    await pool.close()


@pytest.fixture
async def clean_files(db_pool):
    """Clean files before each test."""
    async with db_pool.acquire() as conn:
        await conn.execute("DELETE FROM sandbox_filesystem WHERE thread_id = 'test_maistack_tools'")
    yield


def test_maistack_tools_shim_is_valid_python():
    """Test that MAISTACK_TOOLS_SHIM is valid Python syntax."""
    try:
        ast.parse(MAISTACK_TOOLS_SHIM)
    except SyntaxError as e:
        pytest.fail(f"MAISTACK_TOOLS_SHIM has invalid Python syntax: {e}")


def test_maistack_tools_shim_has_required_functions():
    """Test that MAISTACK_TOOLS_SHIM defines required functions."""
    tree = ast.parse(MAISTACK_TOOLS_SHIM)

    function_names = {
        node.name for node in ast.walk(tree) if isinstance(node, ast.AsyncFunctionDef)
    }

    required_functions = {
        "call_tool",
        "list_collections",
        "search_all_collections",
        "graph_search_all_collections",
    }

    missing = required_functions - function_names
    assert not missing, f"Missing required functions: {missing}"


def test_maistack_tools_shim_imports_mayflower_mcp():
    """Test that MAISTACK_TOOLS_SHIM imports mayflower_mcp."""
    tree = ast.parse(MAISTACK_TOOLS_SHIM)

    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.append(node.module)

    assert "mayflower_mcp" in imports, "MAISTACK_TOOLS_SHIM must import mayflower_mcp"


async def test_write_bootstrap_files_creates_maistack_tools(db_pool, clean_files):
    """Test that write_bootstrap_files creates maistack_tools.py in VFS."""
    vfs = VirtualFilesystem(db_pool, "test_maistack_tools")

    await write_bootstrap_files(vfs, thread_id="test_maistack_tools")

    # Verify maistack_tools.py was created
    entry = await vfs.read_file("/site-packages/maistack_tools.py")
    assert entry is not None
    content = entry["content"].decode("utf-8")

    # Verify content matches the shim
    assert content == MAISTACK_TOOLS_SHIM


async def test_write_bootstrap_files_creates_both_modules(db_pool, clean_files):
    """Test that write_bootstrap_files creates both mayflower_mcp and maistack_tools."""
    vfs = VirtualFilesystem(db_pool, "test_maistack_tools")

    await write_bootstrap_files(vfs, thread_id="test_maistack_tools")

    # Both files should exist
    mcp_entry = await vfs.read_file("/site-packages/mayflower_mcp.py")
    tools_entry = await vfs.read_file("/site-packages/maistack_tools.py")

    assert mcp_entry is not None, "mayflower_mcp.py should be created"
    assert tools_entry is not None, "maistack_tools.py should be created"


def test_search_all_collections_uses_asyncio_gather():
    """Test that search_all_collections uses asyncio.gather for parallelism."""
    tree = ast.parse(MAISTACK_TOOLS_SHIM)

    # Find the search_all_collections function
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "search_all_collections":
            # Check that asyncio.gather is called
            source = ast.unparse(node)
            assert "asyncio.gather" in source, (
                "search_all_collections should use asyncio.gather for parallel execution"
            )
            return

    pytest.fail("search_all_collections function not found")


def test_graph_search_all_collections_uses_asyncio_gather():
    """Test that graph_search_all_collections uses asyncio.gather for parallelism."""
    tree = ast.parse(MAISTACK_TOOLS_SHIM)

    # Find the graph_search_all_collections function
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "graph_search_all_collections":
            # Check that asyncio.gather is called
            source = ast.unparse(node)
            assert "asyncio.gather" in source, (
                "graph_search_all_collections should use asyncio.gather for parallel execution"
            )
            return

    pytest.fail("graph_search_all_collections function not found")


def test_call_tool_uses_maistack_server():
    """Test that call_tool routes calls to 'maistack' server."""
    tree = ast.parse(MAISTACK_TOOLS_SHIM)

    # Find the call_tool function
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "call_tool":
            source = ast.unparse(node)
            assert '"maistack"' in source or "'maistack'" in source, (
                "call_tool should route calls to 'maistack' server"
            )
            return

    pytest.fail("call_tool function not found")
