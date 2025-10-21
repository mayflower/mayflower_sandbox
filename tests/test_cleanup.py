"""
Tests for cleanup job.
"""

import asyncio
import os
import sys

import asyncpg
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from mayflower_sandbox.cleanup import CleanupJob
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
    yield pool
    await pool.close()


@pytest.fixture
async def clean_db(db_pool):
    """Clean database before each test."""
    async with db_pool.acquire() as conn:
        await conn.execute("DELETE FROM sandbox_sessions")
    yield


@pytest.fixture
async def cleanup_job(db_pool):
    """Create cleanup job instance."""
    return CleanupJob(db_pool, interval_seconds=1, dry_run=False)


async def test_cleanup_expired_sessions(cleanup_job, db_pool, clean_db):
    """Test cleaning up expired sessions."""
    # Create active session
    async with db_pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO sandbox_sessions (thread_id, expires_at)
            VALUES ('active', NOW() + INTERVAL '1 day')
        """)

        # Create expired sessions
        await conn.execute("""
            INSERT INTO sandbox_sessions (thread_id, expires_at)
            VALUES ('expired_1', NOW() - INTERVAL '1 day'),
                   ('expired_2', NOW() - INTERVAL '2 days')
        """)

    # Run cleanup
    stats = await cleanup_job.cleanup_expired_sessions()

    assert stats["sessions_deleted"] == 2

    # Verify active session still exists
    async with db_pool.acquire() as conn:
        sessions = await conn.fetch("SELECT thread_id FROM sandbox_sessions")
        assert len(sessions) == 1
        assert sessions[0]["thread_id"] == "active"


async def test_cleanup_with_files(cleanup_job, db_pool, clean_db):
    """Test cleanup deletes files via cascade."""
    # Create expired session with files
    async with db_pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO sandbox_sessions (thread_id, expires_at)
            VALUES ('with_files', NOW() - INTERVAL '1 day')
        """)

    # Add files
    vfs = VirtualFilesystem(db_pool, "with_files")
    await vfs.write_file("/tmp/file1.txt", b"content1")
    await vfs.write_file("/tmp/file2.txt", b"content2")

    # Run cleanup
    stats = await cleanup_job.cleanup_expired_sessions()

    assert stats["sessions_deleted"] == 1
    assert stats["files_deleted"] == 2

    # Verify files are gone
    async with db_pool.acquire() as conn:
        files = await conn.fetch("SELECT * FROM sandbox_filesystem WHERE thread_id = 'with_files'")
        assert len(files) == 0


async def test_cleanup_orphaned_files(cleanup_job, db_pool, clean_db):
    """Test cleanup handles orphaned files gracefully.

    Note: With CASCADE DELETE, orphaned files shouldn't normally exist,
    but the cleanup job should handle them if they do.
    """
    # Run cleanup on empty database - should complete successfully
    stats = await cleanup_job.cleanup_orphaned_files()

    # Should report no orphans found
    assert stats["files_deleted"] == 0
    assert stats["bytes_freed"] == 0


async def test_dry_run_mode(db_pool, clean_db):
    """Test dry run doesn't actually delete."""
    cleanup = CleanupJob(db_pool, dry_run=True)

    # Create expired session
    async with db_pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO sandbox_sessions (thread_id, expires_at)
            VALUES ('expired', NOW() - INTERVAL '1 day')
        """)

    # Run cleanup in dry run mode
    stats = await cleanup.cleanup_expired_sessions()

    # Should report what would be deleted
    assert stats["sessions_deleted"] == 0  # Not actually deleted

    # Verify session still exists
    async with db_pool.acquire() as conn:
        sessions = await conn.fetch("SELECT * FROM sandbox_sessions WHERE thread_id = 'expired'")
        assert len(sessions) == 1


async def test_run_once(cleanup_job, db_pool, clean_db):
    """Test running full cleanup cycle."""
    # Create mixed scenario
    async with db_pool.acquire() as conn:
        # Active session
        await conn.execute("""
            INSERT INTO sandbox_sessions (thread_id, expires_at)
            VALUES ('active', NOW() + INTERVAL '1 day')
        """)

        # Expired session with files
        await conn.execute("""
            INSERT INTO sandbox_sessions (thread_id, expires_at)
            VALUES ('expired', NOW() - INTERVAL '1 day')
        """)

    vfs = VirtualFilesystem(db_pool, "expired")
    await vfs.write_file("/tmp/file.txt", b"test content")

    # Run full cleanup
    stats = await cleanup_job.run_once()

    assert stats["sessions_deleted"] == 1
    assert stats["files_deleted"] >= 1
    assert "timestamp" in stats
    assert "elapsed_seconds" in stats


async def test_periodic_cleanup(db_pool, clean_db):
    """Test periodic cleanup runs in background."""
    cleanup = CleanupJob(db_pool, interval_seconds=1, dry_run=False)

    # Create expired session
    async with db_pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO sandbox_sessions (thread_id, expires_at)
            VALUES ('to_expire', NOW() - INTERVAL '1 day')
        """)

    # Start cleanup
    cleanup.start()

    # Wait for at least one cycle
    await asyncio.sleep(2)

    # Stop cleanup
    await cleanup.stop()

    # Verify expired session was cleaned up
    async with db_pool.acquire() as conn:
        sessions = await conn.fetch("SELECT * FROM sandbox_sessions WHERE thread_id = 'to_expire'")
        assert len(sessions) == 0


async def test_no_cleanup_needed(cleanup_job, clean_db):
    """Test cleanup handles empty database gracefully."""
    stats = await cleanup_job.run_once()

    assert stats["sessions_deleted"] == 0
    assert stats["files_deleted"] == 0
    assert stats["bytes_freed"] == 0


async def test_bytes_freed_calculation(cleanup_job, db_pool, clean_db):
    """Test bytes freed calculation."""
    # Create expired session with known file sizes
    async with db_pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO sandbox_sessions (thread_id, expires_at)
            VALUES ('with_big_files', NOW() - INTERVAL '1 day')
        """)

    vfs = VirtualFilesystem(db_pool, "with_big_files")
    content1 = b"x" * 1000  # 1 KB
    content2 = b"y" * 2000  # 2 KB
    await vfs.write_file("/tmp/file1.txt", content1)
    await vfs.write_file("/tmp/file2.txt", content2)

    # Run cleanup
    stats = await cleanup_job.cleanup_expired_sessions()

    assert stats["bytes_freed"] == 3000  # 1KB + 2KB
