"""
Tests for file serving HTTP API.
"""

import pytest
import asyncpg
import os
import sys
from aiohttp.test_utils import TestClient, TestServer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from mayflower_sandbox.server import FileServer
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
            VALUES ('test_server', NOW() + INTERVAL '1 day')
            ON CONFLICT (thread_id) DO NOTHING
        """
        )

    yield pool
    await pool.close()


@pytest.fixture
async def clean_files(db_pool):
    """Clean files before each test."""
    async with db_pool.acquire() as conn:
        await conn.execute("DELETE FROM sandbox_filesystem WHERE thread_id = 'test_server'")
    yield


@pytest.fixture
async def server(db_pool):
    """Create file server instance."""
    return FileServer(db_pool, host="127.0.0.1", port=8888)


@pytest.fixture
async def client(server):
    """Create test client."""
    async with TestClient(TestServer(server.app)) as client:
        yield client


async def test_health_check(client):
    """Test health check endpoint."""
    resp = await client.get("/health")
    assert resp.status == 200

    data = await resp.json()
    assert data["status"] == "healthy"
    assert data["service"] == "mayflower-sandbox"


async def test_serve_file(client, db_pool, clean_files):
    """Test serving a file."""
    # Create a file in VFS
    vfs = VirtualFilesystem(db_pool, "test_server")
    await vfs.write_file("/tmp/test.txt", b"Hello from file server!")

    # Request the file
    resp = await client.get("/files/test_server/tmp/test.txt")
    assert resp.status == 200

    content = await resp.read()
    assert content == b"Hello from file server!"

    # Check content type
    assert resp.headers["Content-Type"] == "text/plain"


async def test_serve_file_not_found(client, clean_files):
    """Test serving non-existent file returns 404."""
    resp = await client.get("/files/test_server/tmp/nonexistent.txt")
    assert resp.status == 404

    data = await resp.json()
    assert "error" in data
    assert "not found" in data["error"].lower()


async def test_serve_file_with_disposition(client, db_pool, clean_files):
    """Test file disposition (inline vs attachment)."""
    vfs = VirtualFilesystem(db_pool, "test_server")
    await vfs.write_file("/tmp/download.csv", b"a,b,c\n1,2,3")

    # Test inline (default)
    resp = await client.get("/files/test_server/tmp/download.csv")
    assert 'inline; filename="download.csv"' in resp.headers["Content-Disposition"]

    # Test attachment (force download)
    resp = await client.get("/files/test_server/tmp/download.csv?disposition=attachment")
    assert 'attachment; filename="download.csv"' in resp.headers["Content-Disposition"]


async def test_list_files(client, db_pool, clean_files):
    """Test listing files."""
    # Create some files
    vfs = VirtualFilesystem(db_pool, "test_server")
    await vfs.write_file("/tmp/file1.txt", b"content1")
    await vfs.write_file("/tmp/file2.txt", b"content2")
    await vfs.write_file("/data/file3.csv", b"a,b,c")

    # List all files
    resp = await client.get("/files/test_server")
    assert resp.status == 200

    data = await resp.json()
    assert data["thread_id"] == "test_server"
    assert data["count"] == 3
    assert len(data["files"]) == 3

    # Check file structure
    file_paths = {f["file_path"] for f in data["files"]}
    assert "/tmp/file1.txt" in file_paths
    assert "/tmp/file2.txt" in file_paths
    assert "/data/file3.csv" in file_paths


async def test_list_files_with_prefix(client, db_pool, clean_files):
    """Test listing files with prefix filter."""
    vfs = VirtualFilesystem(db_pool, "test_server")
    await vfs.write_file("/tmp/file1.txt", b"content1")
    await vfs.write_file("/tmp/file2.txt", b"content2")
    await vfs.write_file("/data/file3.csv", b"a,b,c")

    # List only /tmp/ files
    resp = await client.get("/files/test_server?prefix=/tmp/")
    assert resp.status == 200

    data = await resp.json()
    assert data["count"] == 2

    file_paths = {f["file_path"] for f in data["files"]}
    assert "/tmp/file1.txt" in file_paths
    assert "/tmp/file2.txt" in file_paths
    assert "/data/file3.csv" not in file_paths


async def test_serve_binary_file(client, db_pool, clean_files):
    """Test serving binary file."""
    vfs = VirtualFilesystem(db_pool, "test_server")

    # Create a binary file (PNG magic bytes)
    png_data = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
    await vfs.write_file("/tmp/image.png", png_data)

    # Request the file
    resp = await client.get("/files/test_server/tmp/image.png")
    assert resp.status == 200

    content = await resp.read()
    assert content == png_data
    assert resp.headers["Content-Type"] == "image/png"


async def test_thread_isolation(client, db_pool):
    """Test files are isolated by thread_id."""
    # Clean both threads
    async with db_pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM sandbox_filesystem WHERE thread_id IN ('thread_a', 'thread_b')"
        )
        await conn.execute("""
            INSERT INTO sandbox_sessions (thread_id, expires_at)
            VALUES ('thread_a', NOW() + INTERVAL '1 day'),
                   ('thread_b', NOW() + INTERVAL '1 day')
            ON CONFLICT (thread_id) DO NOTHING
        """)

    # Create files in different threads
    vfs_a = VirtualFilesystem(db_pool, "thread_a")
    vfs_b = VirtualFilesystem(db_pool, "thread_b")

    await vfs_a.write_file("/tmp/secret.txt", b"Thread A data")
    await vfs_b.write_file("/tmp/secret.txt", b"Thread B data")

    # Request from thread A
    resp = await client.get("/files/thread_a/tmp/secret.txt")
    content = await resp.read()
    assert content == b"Thread A data"

    # Request from thread B
    resp = await client.get("/files/thread_b/tmp/secret.txt")
    content = await resp.read()
    assert content == b"Thread B data"
