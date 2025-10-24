import os
import sys

import asyncpg
import pytest

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from mayflower_sandbox.filesystem import VirtualFilesystem
from mayflower_sandbox.tools.file_edit import FileEditTool


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
            VALUES ('test_edit_thread', NOW() + INTERVAL '1 day')
            ON CONFLICT (thread_id) DO NOTHING
        """)

    return VirtualFilesystem(db_pool, "test_edit_thread")


@pytest.fixture
async def edit_tool(db_pool):
    """Create FileEditTool instance."""
    return FileEditTool(db_pool=db_pool, thread_id="test_edit_thread")


@pytest.fixture
async def clean_files(db_pool):
    """Clean filesystem before each test."""
    async with db_pool.acquire() as conn:
        await conn.execute("DELETE FROM sandbox_filesystem WHERE thread_id = 'test_edit_thread'")
    yield


async def test_basic_edit(filesystem, edit_tool, clean_files):
    """Test basic string replacement."""
    # Create a file
    content = "Hello World\nThis is a test\nGoodbye World"
    await filesystem.write_file("/tmp/test.txt", content.encode())

    # Edit the file
    result = await edit_tool._arun(
        file_path="/tmp/test.txt",
        old_string="Hello World",
        new_string="Hello Universe",
    )

    assert "Successfully edited" in result
    assert "Hello World" in result
    assert "Hello Universe" in result

    # Verify file was modified
    file_data = await filesystem.read_file("/tmp/test.txt")
    new_content = file_data["content"].decode()
    assert "Hello Universe" in new_content
    assert "Hello World" not in new_content
    assert "This is a test" in new_content  # Other content unchanged


async def test_string_not_found(filesystem, edit_tool, clean_files):
    """Test error when string doesn't exist."""
    content = "Hello World"
    await filesystem.write_file("/tmp/test.txt", content.encode())

    result = await edit_tool._arun(
        file_path="/tmp/test.txt",
        old_string="Nonexistent",
        new_string="Something",
    )

    assert "Error" in result
    assert "not found" in result.lower()


async def test_string_not_unique(filesystem, edit_tool, clean_files):
    """Test error when string appears multiple times."""
    content = "Hello World\nHello World\nHello World"
    await filesystem.write_file("/tmp/test.txt", content.encode())

    result = await edit_tool._arun(
        file_path="/tmp/test.txt",
        old_string="Hello World",
        new_string="Hi There",
    )

    assert "Error" in result
    assert "3 times" in result
    assert "must be unique" in result.lower()

    # Verify file was NOT modified
    file_data = await filesystem.read_file("/tmp/test.txt")
    assert file_data["content"].decode() == content


async def test_file_not_found(edit_tool):
    """Test error when file doesn't exist."""
    result = await edit_tool._arun(
        file_path="/tmp/nonexistent.txt",
        old_string="anything",
        new_string="something",
    )

    assert "Error" in result
    assert "not found" in result.lower()


async def test_multiline_edit(filesystem, edit_tool, clean_files):
    """Test editing multiline strings."""
    content = """def hello():
    print("Hello")
    return True"""

    await filesystem.write_file("/tmp/script.py", content.encode())

    result = await edit_tool._arun(
        file_path="/tmp/script.py",
        old_string='def hello():\n    print("Hello")\n    return True',
        new_string='def hello():\n    print("Hi")\n    return False',
    )

    assert "Successfully edited" in result

    # Verify
    file_data = await filesystem.read_file("/tmp/script.py")
    new_content = file_data["content"].decode()
    assert 'print("Hi")' in new_content
    assert "return False" in new_content


async def test_code_modification(filesystem, edit_tool, clean_files):
    """Test realistic code modification scenario."""
    code = """
DEBUG = False
API_KEY = "secret"
MAX_RETRIES = 3
"""
    await filesystem.write_file("/tmp/config.py", code.encode())

    # Change DEBUG flag
    result = await edit_tool._arun(
        file_path="/tmp/config.py",
        old_string="DEBUG = False",
        new_string="DEBUG = True",
    )

    assert "Successfully edited" in result

    # Verify
    file_data = await filesystem.read_file("/tmp/config.py")
    new_content = file_data["content"].decode()
    assert "DEBUG = True" in new_content
    assert 'API_KEY = "secret"' in new_content  # Other lines unchanged
    assert "MAX_RETRIES = 3" in new_content


async def test_unique_context_required(filesystem, edit_tool, clean_files):
    """Test that providing more context makes string unique."""
    content = """
x = 1
y = 2
x = 3
"""
    await filesystem.write_file("/tmp/vars.py", content.encode())

    # "x = " appears twice, should fail
    result = await edit_tool._arun(
        file_path="/tmp/vars.py",
        old_string="x = ",
        new_string="x = ",
    )
    assert "Error" in result
    assert "2 times" in result

    # But with more context, it's unique
    result = await edit_tool._arun(
        file_path="/tmp/vars.py",
        old_string="x = 1",
        new_string="x = 10",
    )
    assert "Successfully edited" in result

    # Verify only first x was changed
    file_data = await filesystem.read_file("/tmp/vars.py")
    new_content = file_data["content"].decode()
    assert "x = 10" in new_content
    assert "x = 3" in new_content
    # x = 1 should have been replaced
    lines = new_content.strip().split("\n")
    assert "x = 1" not in lines


async def test_tool_name_and_description(edit_tool):
    """Test tool has correct name and description."""
    assert edit_tool.name == "file_edit"
    assert "unique string" in edit_tool.description.lower()
    assert "exactly once" in edit_tool.description.lower()
