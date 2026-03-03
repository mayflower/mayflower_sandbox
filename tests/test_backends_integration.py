"""Integration tests for PostgresBackend and MayflowerSandboxBackend.

These tests verify the backends work correctly with real PostgreSQL
and actual code execution via Pyodide/BusyBox.

Requires:
- Docker PostgreSQL container running on port 5433
- Deno installed for Pyodide execution
"""

import os
import uuid

import pytest

# Import backends - they provide fallback types when deepagents is not installed
from mayflower_sandbox import MayflowerSandboxBackend, PostgresBackend


@pytest.fixture
async def db_pool():
    """Create a database connection pool for testing."""
    import asyncpg

    port = int(os.environ.get("POSTGRES_PORT", "5433"))
    password = os.environ.get("POSTGRES_PASSWORD", "postgres")  # noqa: S105
    pool = await asyncpg.create_pool(
        host="localhost",
        port=port,
        user="postgres",
        password=password,
        database="mayflower_test",
        min_size=1,
        max_size=5,
    )
    yield pool
    await pool.close()


@pytest.fixture
def thread_id():
    """Generate unique thread ID for test isolation."""
    return f"test-{uuid.uuid4().hex[:8]}"


@pytest.fixture
async def cleanup_files(db_pool, thread_id):
    """Clean up test files after each test."""
    yield
    async with db_pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM sandbox_filesystem WHERE thread_id = $1",
            thread_id,
        )
        await conn.execute(
            "DELETE FROM sandbox_sessions WHERE thread_id = $1",
            thread_id,
        )


# =============================================================================
# PostgresBackend Integration Tests
# =============================================================================


class TestPostgresBackendIntegration:
    """Integration tests for PostgresBackend file operations."""

    @pytest.mark.asyncio
    async def test_write_and_read_file(self, db_pool, thread_id, cleanup_files):
        """Test writing and reading a file."""
        backend = PostgresBackend(db_pool, thread_id)

        # Write file
        result = await backend.awrite("/test/hello.txt", "Hello, World!")
        assert result.error is None
        assert result.path == "/test/hello.txt"

        # Read file
        content = await backend.aread("/test/hello.txt")
        assert "Hello, World!" in content
        assert "1\t" in content  # Line numbers

    @pytest.mark.asyncio
    async def test_write_file_already_exists(self, db_pool, thread_id, cleanup_files):
        """Test that writing to existing file fails."""
        backend = PostgresBackend(db_pool, thread_id)

        # Write first time
        result1 = await backend.awrite("/test/exists.txt", "first")
        assert result1.error is None

        # Write second time should fail
        result2 = await backend.awrite("/test/exists.txt", "second")
        assert result2.error is not None
        assert "already exists" in result2.error

    @pytest.mark.asyncio
    async def test_edit_file(self, db_pool, thread_id, cleanup_files):
        """Test editing a file."""
        backend = PostgresBackend(db_pool, thread_id)

        # Write file
        await backend.awrite("/test/edit.txt", "Hello, World!")

        # Edit file
        result = await backend.aedit("/test/edit.txt", "World", "Universe")
        assert result.error is None
        assert result.occurrences == 1

        # Verify edit
        content = await backend.aread("/test/edit.txt")
        assert "Universe" in content
        assert "World" not in content

    @pytest.mark.asyncio
    async def test_edit_replace_all(self, db_pool, thread_id, cleanup_files):
        """Test editing with replace_all flag."""
        backend = PostgresBackend(db_pool, thread_id)

        # Write file with duplicates
        await backend.awrite("/test/multi.txt", "foo bar foo baz foo")

        # Edit without replace_all should fail
        result1 = await backend.aedit("/test/multi.txt", "foo", "qux")
        assert result1.error is not None
        assert "appears 3 times" in result1.error

        # Edit with replace_all should succeed
        result2 = await backend.aedit("/test/multi.txt", "foo", "qux", replace_all=True)
        assert result2.error is None
        assert result2.occurrences == 3

    @pytest.mark.asyncio
    async def test_ls_info(self, db_pool, thread_id, cleanup_files):
        """Test listing directory contents."""
        backend = PostgresBackend(db_pool, thread_id)

        # Create files
        await backend.awrite("/app/main.py", "print('main')")
        await backend.awrite("/app/utils.py", "def helper(): pass")
        await backend.awrite("/app/config/settings.json", "{}")

        # List /app
        infos = await backend.als_info("/app")
        paths = [info["path"] for info in infos]

        assert "/app/main.py" in paths
        assert "/app/utils.py" in paths
        assert "/app/config/" in paths  # Directory

    @pytest.mark.asyncio
    async def test_glob_info(self, db_pool, thread_id, cleanup_files):
        """Test glob pattern matching."""
        backend = PostgresBackend(db_pool, thread_id)

        # Create files
        await backend.awrite("/src/main.py", "main")
        await backend.awrite("/src/test.py", "test")
        await backend.awrite("/src/readme.md", "readme")

        # Glob for .py files
        infos = await backend.aglob_info("*.py", "/src")
        paths = [info["path"] for info in infos]

        assert "/src/main.py" in paths
        assert "/src/test.py" in paths
        assert "/src/readme.md" not in paths

    @pytest.mark.asyncio
    async def test_grep_raw(self, db_pool, thread_id, cleanup_files):
        """Test searching file contents."""
        backend = PostgresBackend(db_pool, thread_id)

        # Create files
        await backend.awrite("/code/a.py", "def hello():\n    return 'hello'")
        await backend.awrite("/code/b.py", "def goodbye():\n    return 'goodbye'")

        # Search for 'hello'
        matches = await backend.agrep_raw("hello", "/code")
        assert isinstance(matches, list)
        assert len(matches) == 2  # 'hello' appears in function name and string

        # Search for 'goodbye'
        matches = await backend.agrep_raw("goodbye", "/code")
        assert len(matches) == 2

    @pytest.mark.asyncio
    async def test_upload_and_download_files(self, db_pool, thread_id, cleanup_files):
        """Test batch file upload and download."""
        backend = PostgresBackend(db_pool, thread_id)

        # Upload files
        files = [
            ("/data/file1.txt", b"content1"),
            ("/data/file2.txt", b"content2"),
        ]
        upload_results = await backend.aupload_files(files)

        assert len(upload_results) == 2
        assert all(r.error is None for r in upload_results)

        # Download files
        download_results = await backend.adownload_files(["/data/file1.txt", "/data/file2.txt"])

        assert len(download_results) == 2
        assert download_results[0].content == b"content1"
        assert download_results[1].content == b"content2"

    @pytest.mark.asyncio
    async def test_download_nonexistent_file(self, db_pool, thread_id, cleanup_files):
        """Test downloading a file that doesn't exist."""
        backend = PostgresBackend(db_pool, thread_id)

        results = await backend.adownload_files(["/nonexistent.txt"])
        assert len(results) == 1
        assert results[0].error == "file_not_found"
        assert results[0].content is None

    @pytest.mark.asyncio
    async def test_files_update_returned(self, db_pool, thread_id, cleanup_files):
        """Test that write/edit return files_update for state sync."""
        backend = PostgresBackend(db_pool, thread_id)

        # Write should return files_update
        write_result = await backend.awrite("/state/test.txt", "content")
        assert write_result.files_update is not None
        assert "/state/test.txt" in write_result.files_update

        # Edit should return files_update
        edit_result = await backend.aedit("/state/test.txt", "content", "updated")
        assert edit_result.files_update is not None
        assert "/state/test.txt" in edit_result.files_update


# =============================================================================
# MayflowerSandboxBackend Integration Tests
# =============================================================================


class TestMayflowerSandboxBackendIntegration:
    """Integration tests for MayflowerSandboxBackend with execution."""

    @pytest.mark.asyncio
    async def test_inherits_file_operations(self, db_pool, thread_id, cleanup_files):
        """Test that sandbox backend inherits PostgresBackend file operations."""
        backend = MayflowerSandboxBackend(db_pool, thread_id)

        # Write and read (inherited from PostgresBackend)
        result = await backend.awrite("/test/file.txt", "test content")
        assert result.error is None

        content = await backend.aread("/test/file.txt")
        assert "test content" in content

    @pytest.mark.asyncio
    async def test_execute_shell_command(self, db_pool, thread_id, cleanup_files):
        """Test executing shell commands via BusyBox."""
        backend = MayflowerSandboxBackend(db_pool, thread_id)

        # Simple echo command
        result = await backend.aexecute("echo 'Hello from shell'")
        assert result.exit_code == 0
        assert "Hello from shell" in result.output

    @pytest.mark.asyncio
    async def test_execute_python_script(self, db_pool, thread_id, cleanup_files):
        """Test executing Python scripts via Pyodide."""
        backend = MayflowerSandboxBackend(db_pool, thread_id)

        # Write Python script
        script = """
print("Hello from Python!")
x = 1 + 2
print(f"Result: {x}")
"""
        await backend.awrite("/app/script.py", script)

        # Execute script
        result = await backend.aexecute("python /app/script.py")
        assert result.exit_code == 0
        assert "Hello from Python!" in result.output
        assert "Result: 3" in result.output

    @pytest.mark.asyncio
    async def test_execute_python_with_args(self, db_pool, thread_id, cleanup_files):
        """Test executing Python script with command line arguments."""
        backend = MayflowerSandboxBackend(db_pool, thread_id)

        # Write script that uses sys.argv
        script = """
import sys
print(f"Script: {sys.argv[0]}")
print(f"Args: {sys.argv[1:]}")
"""
        await backend.awrite("/app/args.py", script)

        # Execute with arguments
        result = await backend.aexecute("python /app/args.py arg1 arg2 arg3")
        assert result.exit_code == 0
        assert "arg1" in result.output
        assert "arg2" in result.output
        assert "arg3" in result.output

    @pytest.mark.asyncio
    async def test_execute_python_script_not_found(self, db_pool, thread_id, cleanup_files):
        """Test executing non-existent Python script."""
        backend = MayflowerSandboxBackend(db_pool, thread_id)

        result = await backend.aexecute("python /nonexistent.py")
        assert result.exit_code == 2
        assert "No such file" in result.output

    @pytest.mark.asyncio
    async def test_execute_python_with_error(self, db_pool, thread_id, cleanup_files):
        """Test executing Python script that raises an error."""
        backend = MayflowerSandboxBackend(db_pool, thread_id)

        script = """
raise ValueError("Test error message")
"""
        await backend.awrite("/app/error.py", script)

        result = await backend.aexecute("python /app/error.py")
        assert result.exit_code == 1
        assert "ValueError" in result.output
        assert "Test error message" in result.output

    @pytest.mark.asyncio
    async def test_execute_shell_with_file_operations(self, db_pool, thread_id, cleanup_files):
        """Test shell commands that interact with the filesystem."""
        backend = MayflowerSandboxBackend(db_pool, thread_id)

        # Create a file via backend (trailing newline for proper wc -l count)
        await backend.awrite("/data/input.txt", "line1\nline2\nline3\n")

        # Use shell to read and process
        result = await backend.aexecute("cat /data/input.txt | wc -l")
        assert result.exit_code == 0
        # Output should contain "3" (three lines, wc -l counts newlines)
        assert "3" in result.output.strip()

    @pytest.mark.asyncio
    async def test_id_property(self, db_pool, thread_id, cleanup_files):
        """Test that sandbox has unique ID."""
        backend = MayflowerSandboxBackend(db_pool, thread_id)

        assert backend.id == f"mayflower:{thread_id}"

    @pytest.mark.asyncio
    async def test_python_file_io(self, db_pool, thread_id, cleanup_files):
        """Test Python script reading and writing files."""
        backend = MayflowerSandboxBackend(db_pool, thread_id)

        # Write input file
        await backend.awrite("/work/input.txt", "Hello World")

        # Write Python script that reads and transforms
        script = """
with open('/work/input.txt') as f:
    content = f.read()

output = content.upper()

with open('/work/output.txt', 'w') as f:
    f.write(output)

print("Done!")
"""
        await backend.awrite("/work/transform.py", script)

        # Execute
        result = await backend.aexecute("python /work/transform.py")
        assert result.exit_code == 0
        assert "Done!" in result.output

        # Verify output file was created
        # Note: Files created in Pyodide are synced back to VFS
        content = await backend.aread("/work/output.txt")
        assert "HELLO WORLD" in content
