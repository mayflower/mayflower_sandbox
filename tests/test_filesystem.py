import os
import sys

import asyncpg
import pytest

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from mayflower_sandbox.filesystem import (
    FileNotFoundError,
    FileTooLargeError,
    InvalidPathError,
    VirtualFilesystem,
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
    yield pool
    await pool.close()


@pytest.fixture
async def filesystem(db_pool):
    """Create VirtualFilesystem instance."""
    # Create session first
    async with db_pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO sandbox_sessions (thread_id, expires_at)
            VALUES ('test_thread', NOW() + INTERVAL '1 day')
            ON CONFLICT (thread_id) DO NOTHING
        """)

    return VirtualFilesystem(db_pool, "test_thread")


@pytest.fixture
async def clean_files(db_pool):
    """Clean filesystem before each test."""
    async with db_pool.acquire() as conn:
        await conn.execute("DELETE FROM sandbox_filesystem WHERE thread_id = 'test_thread'")
    yield


async def test_write_and_read_file(filesystem, clean_files):
    """Test basic write and read operations."""
    content = b"Hello, World!"
    metadata = await filesystem.write_file("/tmp/test.txt", content)

    assert metadata["file_path"] == "/tmp/test.txt"
    assert metadata["size"] == len(content)
    assert metadata["content_type"] == "text/plain"

    # Read back
    file_data = await filesystem.read_file("/tmp/test.txt")
    assert file_data["content"] == content


async def test_write_binary_file(filesystem, clean_files):
    """Test writing binary file."""
    # PNG magic bytes
    png_content = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100

    metadata = await filesystem.write_file("/tmp/image.png", png_content)
    assert metadata["content_type"] == "image/png"


async def test_file_too_large(filesystem, clean_files):
    """Test file size limit enforcement."""
    large_content = b"x" * (21 * 1024 * 1024)  # 21 MB

    with pytest.raises(FileTooLargeError):
        await filesystem.write_file("/tmp/large.bin", large_content)


async def test_invalid_path_traversal(filesystem, clean_files):
    """Test path traversal is rejected."""
    with pytest.raises(InvalidPathError):
        await filesystem.write_file("../etc/passwd", b"malicious")


async def test_path_normalization(filesystem, clean_files):
    """Test path normalization."""
    content = b"test"

    # Write with various path formats
    await filesystem.write_file("tmp/test.txt", content)
    await filesystem.write_file("/tmp/test2.txt", content)
    await filesystem.write_file("./tmp/test3.txt", content)

    # All should be normalized
    files = await filesystem.list_files()
    paths = [f["file_path"] for f in files]

    assert "/tmp/test.txt" in paths
    assert "/tmp/test2.txt" in paths
    assert "/tmp/test3.txt" in paths


async def test_overwrite_file(filesystem, clean_files):
    """Test overwriting existing file."""
    # Write initial content
    await filesystem.write_file("/tmp/file.txt", b"version 1")

    # Overwrite
    await filesystem.write_file("/tmp/file.txt", b"version 2")

    # Read back
    file_data = await filesystem.read_file("/tmp/file.txt")
    assert file_data["content"] == b"version 2"


async def test_delete_file(filesystem, clean_files):
    """Test file deletion."""
    await filesystem.write_file("/tmp/delete_me.txt", b"temp")

    # Delete
    deleted = await filesystem.delete_file("/tmp/delete_me.txt")
    assert deleted is True

    # Verify gone
    exists = await filesystem.file_exists("/tmp/delete_me.txt")
    assert exists is False


async def test_list_files_with_pattern(filesystem, clean_files):
    """Test listing files with pattern."""
    # Create multiple files
    await filesystem.write_file("/tmp/test1.txt", b"1")
    await filesystem.write_file("/tmp/test2.txt", b"2")
    await filesystem.write_file("/data/file.csv", b"csv")

    # List with pattern
    txt_files = await filesystem.list_files("/tmp/%.txt")
    assert len(txt_files) == 2

    all_files = await filesystem.list_files()
    assert len(all_files) == 3


async def test_thread_isolation(db_pool, clean_files):
    """Test files are isolated between threads."""
    # Create filesystems for different threads
    async with db_pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO sandbox_sessions (thread_id, expires_at)
            VALUES ('thread_1', NOW() + INTERVAL '1 day'),
                   ('thread_2', NOW() + INTERVAL '1 day')
            ON CONFLICT (thread_id) DO NOTHING
        """)

    fs1 = VirtualFilesystem(db_pool, "thread_1")
    fs2 = VirtualFilesystem(db_pool, "thread_2")

    # Write file in thread 1
    await fs1.write_file("/tmp/file.txt", b"thread 1 data")

    # Should not exist in thread 2
    exists = await fs2.file_exists("/tmp/file.txt")
    assert exists is False

    # Write different file in thread 2
    await fs2.write_file("/tmp/file.txt", b"thread 2 data")

    # Both should have their own versions
    file1 = await fs1.read_file("/tmp/file.txt")
    file2 = await fs2.read_file("/tmp/file.txt")

    assert file1["content"] == b"thread 1 data"
    assert file2["content"] == b"thread 2 data"


async def test_file_not_found(filesystem, clean_files):
    """Test reading non-existent file raises error."""
    with pytest.raises(FileNotFoundError):
        await filesystem.read_file("/tmp/nonexistent.txt")


async def test_get_all_files_for_pyodide(filesystem, clean_files):
    """Test getting all files for Pyodide pre-load."""
    # Create some files
    await filesystem.write_file("/tmp/file1.txt", b"content 1")
    await filesystem.write_file("/tmp/file2.txt", b"content 2")
    await filesystem.write_file("/data/data.csv", b"csv data")

    # Get all files
    files = await filesystem.get_all_files_for_pyodide()

    assert len(files) == 3
    assert files["/tmp/file1.txt"] == b"content 1"
    assert files["/tmp/file2.txt"] == b"content 2"
    assert files["/data/data.csv"] == b"csv data"


async def test_mime_type_detection(filesystem, clean_files):
    """Test MIME type detection for various file types."""
    # Text file
    await filesystem.write_file("/tmp/text.txt", b"plain text")
    file1 = await filesystem.read_file("/tmp/text.txt")
    assert file1["content_type"] == "text/plain"

    # CSV file
    await filesystem.write_file("/data/data.csv", b"a,b,c\n1,2,3")
    file2 = await filesystem.read_file("/data/data.csv")
    assert file2["content_type"] == "text/csv"

    # PNG file
    png_data = b"\x89PNG\r\n\x1a\n" + b"\x00" * 50
    await filesystem.write_file("/images/pic.png", png_data)
    file3 = await filesystem.read_file("/images/pic.png")
    assert file3["content_type"] == "image/png"

    # PDF file
    pdf_data = b"%PDF-1.4\n" + b"\x00" * 50
    await filesystem.write_file("/docs/doc.pdf", pdf_data)
    file4 = await filesystem.read_file("/docs/doc.pdf")
    assert file4["content_type"] == "application/pdf"
