"""
Test FileWriteTool with state-based content extraction.
"""

import os
import sys

import asyncpg
import pytest
from dotenv import load_dotenv

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
load_dotenv()

from mayflower_sandbox.filesystem import VirtualFilesystem  # noqa: E402
from mayflower_sandbox.tools.file_write import FileWriteTool  # noqa: E402


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
            VALUES ('test_file_write', NOW() + INTERVAL '1 day')
            ON CONFLICT (thread_id) DO NOTHING
        """
        )

    yield pool
    await pool.close()


@pytest.fixture
async def clean_files(db_pool):
    """Clean files before each test."""
    async with db_pool.acquire() as conn:
        await conn.execute("DELETE FROM sandbox_filesystem WHERE thread_id = 'test_file_write'")
    yield


async def test_file_write_from_state(db_pool, clean_files):
    """Test that file_write extracts and writes content from state."""
    tool = FileWriteTool(db_pool=db_pool, thread_id="test_file_write")

    # Simulate graph state with pending_content_map
    tool_call_id = "test_tool_call_123"
    state = {
        "pending_content_map": {
            tool_call_id: """name,age,city
Alice,30,New York
Bob,25,San Francisco
Charlie,35,Seattle"""
        }
    }

    # Execute the tool (with tool_call_id = returns Command)
    result = await tool._arun(
        file_path="/tmp/data.csv",
        description="CSV data file",
        _state=state,
        tool_call_id=tool_call_id,
    )

    # Handle Command return type
    from langgraph.types import Command

    if isinstance(result, Command):
        result_str = result.resume
    else:
        result_str = result

    # Verify write succeeded
    assert "Successfully wrote" in result_str
    assert "/tmp/data.csv" in result_str
    assert "Error" not in result_str

    # Verify file was actually written to VFS
    vfs = VirtualFilesystem(db_pool, "test_file_write")
    file_info = await vfs.read_file("/tmp/data.csv")
    content = file_info["content"].decode("utf-8")
    assert "Alice,30,New York" in content
    assert "Charlie,35,Seattle" in content


async def test_file_write_clears_pending_content(db_pool, clean_files):
    """Test that file_write returns Command to clear pending_content_map entry."""
    tool = FileWriteTool(db_pool=db_pool, thread_id="test_file_write")

    tool_call_id = "test_call_123"
    state = {
        "pending_content_map": {
            tool_call_id: '{"key": "value", "number": 42}'
        }
    }

    # Execute with tool_call_id to get Command return
    result = await tool._arun(
        file_path="/tmp/config.json",
        description="JSON configuration",
        _state=state,
        tool_call_id=tool_call_id,
    )

    # Verify Command was returned
    from langgraph.types import Command

    assert isinstance(result, Command)
    assert tool_call_id not in result.update["pending_content_map"]  # Should clear this tool's entry
    assert "/tmp/config.json" in result.update["created_files"]
    assert "Successfully wrote" in result.resume


async def test_file_write_no_content_error(db_pool, clean_files):
    """Test that file_write returns error when state has no content."""
    tool = FileWriteTool(db_pool=db_pool, thread_id="test_file_write")

    # Empty state - no pending_content
    state = {}

    result = await tool._arun(
        file_path="/tmp/empty.txt",
        description="Empty test file",
        _state=state,
        tool_call_id="",
    )

    # Verify error message
    assert "Error" in result
    assert "No content found in graph state" in result


async def test_file_write_with_large_content(db_pool, clean_files):
    """Test that file_write handles large content without issues."""
    tool = FileWriteTool(db_pool=db_pool, thread_id="test_file_write")

    # Generate large content (5000 lines)
    large_content = "\n".join([f"Line {i}: {'x' * 50}" for i in range(5000)])

    tool_call_id = "test_large_123"
    state = {
        "pending_content_map": {
            tool_call_id: large_content
        }
    }

    result = await tool._arun(
        file_path="/tmp/large_file.txt",
        description="Large test file",
        _state=state,
        tool_call_id=tool_call_id,
    )

    # Handle Command return type
    from langgraph.types import Command

    if isinstance(result, Command):
        result_str = result.resume
    else:
        result_str = result

    # Verify write succeeded
    assert "Successfully wrote" in result_str
    assert "Error" not in result_str

    # Verify file size
    vfs = VirtualFilesystem(db_pool, "test_file_write")
    file_info = await vfs.read_file("/tmp/large_file.txt")
    content = file_info["content"].decode("utf-8")
    assert len(content) > 250000  # Should be ~255KB
    assert "Line 4999:" in content
