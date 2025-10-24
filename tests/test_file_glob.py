import os
import sys

import asyncpg
import pytest

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from mayflower_sandbox.filesystem import VirtualFilesystem
from mayflower_sandbox.tools.file_glob import FileGlobTool


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
            VALUES ('test_glob_thread', NOW() + INTERVAL '1 day')
            ON CONFLICT (thread_id) DO NOTHING
        """)

    return VirtualFilesystem(db_pool, "test_glob_thread")


@pytest.fixture
async def glob_tool(db_pool):
    """Create FileGlobTool instance."""
    return FileGlobTool(db_pool=db_pool, thread_id="test_glob_thread")


@pytest.fixture
async def clean_files(db_pool):
    """Clean filesystem before each test."""
    async with db_pool.acquire() as conn:
        await conn.execute("DELETE FROM sandbox_filesystem WHERE thread_id = 'test_glob_thread'")
    yield


async def test_glob_simple_pattern(filesystem, glob_tool, clean_files):
    """Test simple glob pattern matching."""
    # Create test files
    await filesystem.write_file("/tmp/test.py", b"python file")
    await filesystem.write_file("/tmp/test.txt", b"text file")
    await filesystem.write_file("/tmp/data.json", b"{}")

    # Find all .py files
    result = await glob_tool._arun(pattern="*.py")
    assert "/tmp/test.py" in result
    assert ".txt" not in result


async def test_glob_recursive_pattern(filesystem, glob_tool, clean_files):
    """Test recursive ** pattern."""
    # Create nested files
    await filesystem.write_file("/tmp/file.py", b"")
    await filesystem.write_file("/data/script.py", b"")
    await filesystem.write_file("/data/sub/code.py", b"")

    # Find all .py files recursively
    result = await glob_tool._arun(pattern="**/*.py")
    assert "/tmp/file.py" in result
    assert "/data/script.py" in result
    assert "/data/sub/code.py" in result


async def test_glob_directory_prefix(filesystem, glob_tool, clean_files):
    """Test glob with directory prefix."""
    await filesystem.write_file("/tmp/file1.txt", b"")
    await filesystem.write_file("/data/file2.txt", b"")
    await filesystem.write_file("/workspace/file3.txt", b"")

    # Find all .txt files in /data
    result = await glob_tool._arun(pattern="/data/*.txt")
    assert "/data/file2.txt" in result
    assert "/tmp/file1.txt" not in result


async def test_glob_no_matches(glob_tool, clean_files):
    """Test when pattern matches nothing."""
    result = await glob_tool._arun(pattern="*.nonexistent")
    assert "No files found" in result


async def test_glob_multiple_extensions(filesystem, glob_tool, clean_files):
    """Test pattern matching multiple files."""
    await filesystem.write_file("/tmp/a.py", b"")
    await filesystem.write_file("/tmp/b.py", b"")
    await filesystem.write_file("/tmp/c.py", b"")

    result = await glob_tool._arun(pattern="/tmp/*.py")
    assert "Found 3 file(s)" in result
    assert "/tmp/a.py" in result
    assert "/tmp/b.py" in result
    assert "/tmp/c.py" in result


async def test_tool_name_and_description(glob_tool):
    """Test tool has correct name and description."""
    assert glob_tool.name == "file_glob"
    assert "glob pattern" in glob_tool.description.lower()
