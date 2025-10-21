import os
import sys
from datetime import datetime, timedelta

import asyncpg
import pytest

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from mayflower_sandbox.manager import (
    SandboxManager,
    SessionExpiredError,
    SessionNotFoundError,
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
async def manager(db_pool):
    """Create SandboxManager instance."""
    return SandboxManager(db_pool)


@pytest.fixture
async def clean_db(db_pool):
    """Clean database before each test."""
    async with db_pool.acquire() as conn:
        await conn.execute("DELETE FROM sandbox_sessions")
    yield


async def test_create_new_session(manager, clean_db):
    """Test creating a new session."""
    session = await manager.get_or_create_session("thread_123")

    assert session["thread_id"] == "thread_123"
    assert session["created_at"] is not None
    assert session["last_accessed"] is not None
    assert session["expires_at"] > datetime.now()


async def test_get_existing_session(manager, clean_db):
    """Test retrieving existing session."""
    # Create session
    session1 = await manager.get_or_create_session("thread_456")

    # Get same session
    session2 = await manager.get_or_create_session("thread_456")

    assert session1["thread_id"] == session2["thread_id"]
    assert session1["created_at"] == session2["created_at"]


async def test_update_last_accessed(manager, clean_db, db_pool):
    """Test last_accessed timestamp is updated."""
    await manager.get_or_create_session("thread_789")

    # Get original timestamp
    async with db_pool.acquire() as conn:
        original = await conn.fetchrow(
            "SELECT last_accessed FROM sandbox_sessions WHERE thread_id = 'thread_789'"
        )

    # Wait a bit and update
    import asyncio

    await asyncio.sleep(0.1)
    await manager.update_last_accessed("thread_789")

    # Check updated
    async with db_pool.acquire() as conn:
        updated = await conn.fetchrow(
            "SELECT last_accessed FROM sandbox_sessions WHERE thread_id = 'thread_789'"
        )

    assert updated["last_accessed"] > original["last_accessed"]


async def test_expired_session_raises_error(manager, clean_db, db_pool):
    """Test accessing expired session raises error."""
    # Create session with past expiration
    async with db_pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO sandbox_sessions (thread_id, expires_at)
            VALUES ('expired_thread', NOW() - INTERVAL '1 day')
        """)

    # Should raise SessionExpiredError
    with pytest.raises(SessionExpiredError):
        await manager.get_or_create_session("expired_thread")


async def test_cleanup_expired_sessions(manager, clean_db, db_pool):
    """Test cleanup job removes expired sessions."""
    # Create active session
    await manager.get_or_create_session("active_thread")

    # Create expired sessions directly
    async with db_pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO sandbox_sessions (thread_id, expires_at)
            VALUES ('expired_1', NOW() - INTERVAL '1 day'),
                   ('expired_2', NOW() - INTERVAL '2 days')
        """)

    # Run cleanup
    deleted = await manager.cleanup_expired_sessions()

    assert deleted == 2

    # Verify active session still exists
    session = await manager.get_session("active_thread")
    assert session["thread_id"] == "active_thread"


async def test_session_not_found(manager, clean_db):
    """Test getting non-existent session raises error."""
    with pytest.raises(SessionNotFoundError):
        await manager.get_session("nonexistent_thread")


async def test_list_active_sessions(manager, clean_db):
    """Test listing active sessions."""
    # Create multiple sessions
    await manager.get_or_create_session("thread_1")
    await manager.get_or_create_session("thread_2")
    await manager.get_or_create_session("thread_3")

    sessions = await manager.list_active_sessions(limit=10)

    assert len(sessions) == 3
    assert all(s["expires_at"] > datetime.now() for s in sessions)


async def test_session_with_metadata(manager, clean_db):
    """Test creating session with custom metadata."""
    import json

    metadata = {"user_id": "user_123", "project": "test_project"}
    session = await manager.get_or_create_session("thread_meta", metadata=metadata)

    # PostgreSQL returns JSONB as a string, need to parse it
    assert json.loads(session["metadata"]) == metadata


async def test_custom_expiration_period(db_pool, clean_db):
    """Test manager with custom expiration period."""
    # Create manager with 30-day expiration
    manager = SandboxManager(db_pool, default_expiration_days=30)

    session = await manager.get_or_create_session("thread_short")

    # Check expiration is approximately 30 days from now
    expires_at = session["expires_at"]
    expected_expiration = datetime.now() + timedelta(days=30)

    # Allow 1 minute tolerance for test execution time
    time_diff = abs((expires_at - expected_expiration).total_seconds())
    assert time_diff < 60
