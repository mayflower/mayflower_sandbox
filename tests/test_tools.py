"""
Tests for Mayflower Sandbox MCP Tools.
"""

import os
import sys

import asyncpg
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from mayflower_sandbox.tools import (
    ExecutePythonTool,
    FileDeleteTool,
    FileListTool,
    FileReadTool,
    FileWriteTool,
    create_sandbox_tools,
)


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
            VALUES ('test_tools', NOW() + INTERVAL '1 day')
            ON CONFLICT (thread_id) DO NOTHING
        """
        )

    yield pool
    await pool.close()


@pytest.fixture
async def clean_files(db_pool):
    """Clean files before each test."""
    async with db_pool.acquire() as conn:
        await conn.execute("DELETE FROM sandbox_filesystem WHERE thread_id = 'test_tools'")
    yield


async def test_tool_factory_all_tools(db_pool, clean_files):
    """Test creating all tools via factory."""
    tools = create_sandbox_tools(db_pool, "test_tools")

    assert len(tools) == 6
    tool_names = {tool.name for tool in tools}
    assert tool_names == {
        "execute_python",
        "read_file",
        "write_file",
        "list_files",
        "delete_file",
        "str_replace",
    }


async def test_tool_factory_specific_tools(db_pool, clean_files):
    """Test creating specific tools."""
    tools = create_sandbox_tools(
        db_pool, "test_tools", include_tools=["execute_python", "read_file"]
    )

    assert len(tools) == 2
    tool_names = {tool.name for tool in tools}
    assert tool_names == {"execute_python", "read_file"}


async def test_tool_factory_invalid_tool(db_pool, clean_files):
    """Test factory rejects invalid tool names."""
    with pytest.raises(ValueError, match="Unknown tool"):
        create_sandbox_tools(db_pool, "test_tools", include_tools=["invalid_tool"])


async def test_execute_python_tool(db_pool, clean_files):
    """Test ExecutePythonTool."""
    tool = ExecutePythonTool(db_pool=db_pool, thread_id="test_tools")

    # Test successful execution
    result = await tool._arun(code="print('Hello from tool!')")
    assert "Hello from tool!" in result
    assert "Output:" in result

    # Test error handling
    result = await tool._arun(code="raise ValueError('test error')")
    assert "Error:" in result
    assert "ValueError" in result
    assert "test error" in result


async def test_execute_python_tool_with_files(db_pool, clean_files):
    """Test ExecutePythonTool creates files."""
    tool = ExecutePythonTool(db_pool=db_pool, thread_id="test_tools")

    code = """
with open('/tmp/test_output.txt', 'w') as f:
    f.write('Created by tool!')
print('File created')
"""

    result = await tool._arun(code=code)
    assert "File created" in result
    assert "Created files:" in result
    assert "/tmp/test_output.txt" in result


async def test_file_write_and_read_tools(db_pool, clean_files):
    """Test FileWriteTool and FileReadTool."""
    write_tool = FileWriteTool(db_pool=db_pool, thread_id="test_tools")
    read_tool = FileReadTool(db_pool=db_pool, thread_id="test_tools")

    # Write file
    write_result = await write_tool._arun(
        file_path="/tmp/test.txt", content="Hello from write tool!"
    )
    assert "Successfully wrote" in write_result
    assert "/tmp/test.txt" in write_result

    # Read file
    read_result = await read_tool._arun(file_path="/tmp/test.txt")
    assert "Hello from write tool!" in read_result
    assert "File: /tmp/test.txt" in read_result


async def test_file_list_tool(db_pool, clean_files):
    """Test FileListTool."""
    write_tool = FileWriteTool(db_pool=db_pool, thread_id="test_tools")
    list_tool = FileListTool(db_pool=db_pool, thread_id="test_tools")

    # Empty list
    result = await list_tool._arun()
    assert "No files found" in result

    # Write some files
    await write_tool._arun(file_path="/tmp/file1.txt", content="Content 1")
    await write_tool._arun(file_path="/data/file2.csv", content="a,b,c")

    # List all files
    result = await list_tool._arun()
    assert "Found 2 file(s)" in result
    assert "/tmp/file1.txt" in result
    assert "/data/file2.csv" in result

    # List with prefix
    result = await list_tool._arun(prefix="/tmp/")
    assert "Found 1 file(s)" in result
    assert "/tmp/file1.txt" in result
    assert "/data/file2.csv" not in result


async def test_file_delete_tool(db_pool, clean_files):
    """Test FileDeleteTool."""
    write_tool = FileWriteTool(db_pool=db_pool, thread_id="test_tools")
    delete_tool = FileDeleteTool(db_pool=db_pool, thread_id="test_tools")
    list_tool = FileListTool(db_pool=db_pool, thread_id="test_tools")

    # Write file
    await write_tool._arun(file_path="/tmp/to_delete.txt", content="Delete me")

    # Verify exists
    result = await list_tool._arun()
    assert "/tmp/to_delete.txt" in result

    # Delete file
    delete_result = await delete_tool._arun(file_path="/tmp/to_delete.txt")
    assert "Successfully deleted" in delete_result

    # Verify deleted
    result = await list_tool._arun()
    assert "No files found" in result

    # Delete non-existent file
    delete_result = await delete_tool._arun(file_path="/tmp/nonexistent.txt")
    assert "File not found" in delete_result


async def test_integration_workflow(db_pool, clean_files):
    """Test full workflow: write → execute → read → delete."""
    tools = create_sandbox_tools(db_pool, "test_tools")
    tool_map = {tool.name: tool for tool in tools}

    # 1. Write input file
    write_result = await tool_map["write_file"]._arun(
        file_path="/data/input.csv", content="a,b\n1,2\n3,4"
    )
    assert "Successfully wrote" in write_result

    # 2. Execute Python to process file
    code = """
with open('/data/input.csv', 'r') as f:
    lines = f.readlines()

with open('/tmp/output.txt', 'w') as f:
    f.write(f"Processed {len(lines)} lines")

print(f"Processing complete: {len(lines)} lines")
"""
    exec_result = await tool_map["execute_python"]._arun(code=code)
    assert "Processing complete: 3 lines" in exec_result
    assert "/tmp/output.txt" in exec_result

    # 3. List files (filter for user files, not helper modules)
    list_result = await tool_map["list_files"]._arun(prefix="/data/")
    assert "/data/input.csv" in list_result

    list_result = await tool_map["list_files"]._arun(prefix="/tmp/")
    assert "/tmp/output.txt" in list_result

    # 4. Read output
    read_result = await tool_map["read_file"]._arun(file_path="/tmp/output.txt")
    assert "Processed 3 lines" in read_result

    # 5. Clean up
    await tool_map["delete_file"]._arun(file_path="/data/input.csv")
    await tool_map["delete_file"]._arun(file_path="/tmp/output.txt")

    # 6. Verify clean (check user directories, helper modules persist)
    list_result = await tool_map["list_files"]._arun(prefix="/data/")
    assert "No files found" in list_result

    list_result = await tool_map["list_files"]._arun(prefix="/tmp/")
    assert "No files found" in list_result


async def test_thread_isolation(db_pool):
    """Test tools are isolated by thread_id."""
    # Clean both threads
    async with db_pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM sandbox_filesystem WHERE thread_id IN ('thread_1', 'thread_2')"
        )
        await conn.execute("""
            INSERT INTO sandbox_sessions (thread_id, expires_at)
            VALUES ('thread_1', NOW() + INTERVAL '1 day'),
                   ('thread_2', NOW() + INTERVAL '1 day')
            ON CONFLICT (thread_id) DO NOTHING
        """)

    # Create tools for different threads
    tools_1 = create_sandbox_tools(db_pool, "thread_1")
    tools_2 = create_sandbox_tools(db_pool, "thread_2")

    write_1 = next(t for t in tools_1 if t.name == "write_file")
    write_2 = next(t for t in tools_2 if t.name == "write_file")
    list_1 = next(t for t in tools_1 if t.name == "list_files")
    list_2 = next(t for t in tools_2 if t.name == "list_files")

    # Write to thread_1
    await write_1._arun(file_path="/tmp/thread1.txt", content="Thread 1 data")

    # Write to thread_2
    await write_2._arun(file_path="/tmp/thread2.txt", content="Thread 2 data")

    # Thread 1 should only see its file
    result_1 = await list_1._arun()
    assert "/tmp/thread1.txt" in result_1
    assert "/tmp/thread2.txt" not in result_1

    # Thread 2 should only see its file
    result_2 = await list_2._arun()
    assert "/tmp/thread2.txt" in result_2
    assert "/tmp/thread1.txt" not in result_2
