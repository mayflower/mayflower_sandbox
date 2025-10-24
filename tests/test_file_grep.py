import os
import sys

import asyncpg
import pytest

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from mayflower_sandbox.filesystem import VirtualFilesystem
from mayflower_sandbox.tools.file_grep import FileGrepTool


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
    yield pool
    await pool.close()


@pytest.fixture
async def filesystem(db_pool):
    """Create VirtualFilesystem instance."""
    # Create session first
    async with db_pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO sandbox_sessions (thread_id, expires_at)
            VALUES ('test_grep_thread', NOW() + INTERVAL '1 day')
            ON CONFLICT (thread_id) DO NOTHING
        """)

    return VirtualFilesystem(db_pool, "test_grep_thread")


@pytest.fixture
async def grep_tool(db_pool):
    """Create FileGrepTool instance."""
    return FileGrepTool(db_pool=db_pool, thread_id="test_grep_thread")


@pytest.fixture
async def clean_files(db_pool):
    """Clean filesystem before each test."""
    async with db_pool.acquire() as conn:
        await conn.execute("DELETE FROM sandbox_filesystem WHERE thread_id = 'test_grep_thread'")
    yield


async def test_grep_basic_search(filesystem, grep_tool, clean_files):
    """Test basic text search."""
    await filesystem.write_file("/tmp/test.py", b"def hello():\n    print('Hello World')\n")
    await filesystem.write_file("/tmp/other.py", b"def goodbye():\n    pass\n")

    # Search for "hello"
    result = await grep_tool._arun(pattern="hello")
    assert "/tmp/test.py" in result
    assert "/tmp/other.py" not in result


async def test_grep_regex_pattern(filesystem, grep_tool, clean_files):
    """Test regex pattern matching."""
    await filesystem.write_file(
        "/tmp/code.py", b"def func_one():\n    pass\ndef func_two():\n    pass\n"
    )

    # Search for function definitions
    result = await grep_tool._arun(pattern=r"def \w+\(\):")
    assert "/tmp/code.py" in result


async def test_grep_case_insensitive(filesystem, grep_tool, clean_files):
    """Test case-insensitive search."""
    await filesystem.write_file(
        "/tmp/doc.txt", b"ERROR: Something went wrong\nERROR: Another issue\n"
    )

    # Case-insensitive search
    result = await grep_tool._arun(pattern="error", case_insensitive=True)
    assert "/tmp/doc.txt" in result


async def test_grep_output_mode_content(filesystem, grep_tool, clean_files):
    """Test content output mode showing matching lines."""
    await filesystem.write_file("/tmp/log.txt", b"Line 1: INFO\nLine 2: ERROR here\nLine 3: INFO\n")

    # Get matching lines
    result = await grep_tool._arun(pattern="ERROR", output_mode="content")
    assert "Line 2: ERROR here" in result


async def test_grep_output_mode_count(filesystem, grep_tool, clean_files):
    """Test count output mode."""
    await filesystem.write_file("/tmp/file.txt", b"TODO: task 1\nTODO: task 2\nTODO: task 3\n")

    # Count matches
    result = await grep_tool._arun(pattern="TODO", output_mode="count")
    assert "3 match(es)" in result


async def test_grep_no_matches(filesystem, grep_tool, clean_files):
    """Test when pattern matches nothing."""
    await filesystem.write_file("/tmp/empty.txt", b"nothing here\n")

    result = await grep_tool._arun(pattern="nonexistent")
    assert "No matches found" in result


async def test_grep_multiple_files(filesystem, grep_tool, clean_files):
    """Test searching across multiple files."""
    await filesystem.write_file("/tmp/a.py", b"import os\n")
    await filesystem.write_file("/tmp/b.py", b"import sys\n")
    await filesystem.write_file("/tmp/c.py", b"import os\n")

    # Find files with "import os"
    result = await grep_tool._arun(pattern="import os")
    assert "/tmp/a.py" in result
    assert "/tmp/c.py" in result
    assert "/tmp/b.py" not in result


async def test_grep_invalid_regex(grep_tool, clean_files):
    """Test error handling for invalid regex."""
    result = await grep_tool._arun(pattern="[invalid")
    assert "Error" in result
    assert "regex" in result.lower()


async def test_grep_invalid_output_mode(grep_tool, clean_files):
    """Test error handling for invalid output mode."""
    result = await grep_tool._arun(pattern="test", output_mode="invalid_mode")
    assert "Error" in result
    assert "Invalid output_mode" in result


async def test_tool_name_and_description(grep_tool):
    """Test tool has correct name and description."""
    assert grep_tool.name == "file_grep"
    assert "regex" in grep_tool.description.lower()
