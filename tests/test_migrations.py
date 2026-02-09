import os
from pathlib import Path

import asyncpg
import pytest


@pytest.fixture
async def db_pool():
    """Create test database connection pool."""
    # Get database connection info from environment or use defaults
    pg_host = os.getenv("POSTGRES_HOST", "localhost")
    pg_database = os.getenv("POSTGRES_DB", "mayflower_test")
    pg_user = os.getenv("POSTGRES_USER", "postgres")
    pg_password = os.getenv("POSTGRES_PASSWORD", "postgres")
    pg_port = int(os.getenv("POSTGRES_PORT", "5432"))

    # Create test database if it doesn't exist
    try:
        sys_conn = await asyncpg.connect(
            host=pg_host,
            database="postgres",
            user=pg_user,
            password=pg_password,
            port=pg_port,
        )

        # Check if test database exists
        exists = await sys_conn.fetchval(
            "SELECT 1 FROM pg_database WHERE datname = $1", pg_database
        )

        if not exists:
            await sys_conn.execute(f"CREATE DATABASE {pg_database}")

        await sys_conn.close()
    except Exception as e:
        print(f"Warning: Could not create test database: {e}")

    # Connect to test database
    pool = await asyncpg.create_pool(
        host=pg_host,
        database=pg_database,
        user=pg_user,
        password=pg_password,
        port=pg_port,
    )

    # Check if tables already exist (created by CI setup)
    async with pool.acquire() as conn:
        tables_exist = await conn.fetchval("""
            SELECT EXISTS (
                SELECT 1 FROM information_schema.tables
                WHERE table_schema = 'public'
                AND table_name = 'sandbox_sessions'
            )
        """)

    tables_existed_before = tables_exist

    yield pool

    # Cleanup: only drop tables if WE created them (not if they existed before)
    # This prevents breaking other tests when running in CI
    if not tables_existed_before:
        async with pool.acquire() as conn:
            await conn.execute("DROP TABLE IF EXISTS sandbox_session_bytes CASCADE")
            await conn.execute("DROP TABLE IF EXISTS sandbox_filesystem CASCADE")
            await conn.execute("DROP TABLE IF EXISTS sandbox_sessions CASCADE")

    await pool.close()


async def test_migration_creates_tables(db_pool):
    """Verify migration creates all required tables."""
    # Run migration
    migration_path = Path(__file__).parent.parent / "migrations" / "001_sandbox_schema.sql"
    migration_sql = migration_path.read_text()

    async with db_pool.acquire() as conn:
        await conn.execute(migration_sql)

        # Verify tables exist
        tables = await conn.fetch("""
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'public'
            AND table_name LIKE 'sandbox_%'
        """)

        table_names = {t["table_name"] for t in tables}
        assert "sandbox_sessions" in table_names
        assert "sandbox_filesystem" in table_names
        assert "sandbox_session_bytes" in table_names


async def test_foreign_key_cascade(db_pool):
    """Verify cascade delete works."""
    # Run migration first
    migration_path = Path(__file__).parent.parent / "migrations" / "001_sandbox_schema.sql"
    migration_sql = migration_path.read_text()

    async with db_pool.acquire() as conn:
        await conn.execute(migration_sql)

        # Create session
        await conn.execute("""
            INSERT INTO sandbox_sessions (thread_id, expires_at)
            VALUES ('test_123', NOW() + INTERVAL '1 day')
        """)

        # Create file
        await conn.execute(
            """
            INSERT INTO sandbox_filesystem (
                thread_id, file_path, content, content_type, size
            ) VALUES (
                'test_123', '/tmp/test.txt', $1, 'text/plain', 11
            )
        """,
            b"hello world",
        )

        # Delete session
        await conn.execute("DELETE FROM sandbox_sessions WHERE thread_id = 'test_123'")

        # Verify file was cascade deleted
        files = await conn.fetch("""
            SELECT * FROM sandbox_filesystem WHERE thread_id = 'test_123'
        """)
        assert len(files) == 0


async def test_file_size_constraint(db_pool):
    """Verify 20MB file size limit is enforced."""
    # Run migration first
    migration_path = Path(__file__).parent.parent / "migrations" / "001_sandbox_schema.sql"
    migration_sql = migration_path.read_text()

    async with db_pool.acquire() as conn:
        await conn.execute(migration_sql)

        # Create session
        await conn.execute("""
            INSERT INTO sandbox_sessions (thread_id, expires_at)
            VALUES ('test_456', NOW() + INTERVAL '1 day')
        """)

        # Try to insert file larger than 20MB
        large_content = b"x" * (21 * 1024 * 1024)  # 21 MB

        with pytest.raises(asyncpg.CheckViolationError):
            await conn.execute(
                """
                INSERT INTO sandbox_filesystem (
                    thread_id, file_path, content, content_type, size
                ) VALUES (
                    'test_456', '/tmp/large.bin', $1, 'application/octet-stream', $2
                )
            """,
                large_content,
                len(large_content),
            )


async def test_indexes_created(db_pool):
    """Verify all indexes are created."""
    # Run migration first
    migration_path = Path(__file__).parent.parent / "migrations" / "001_sandbox_schema.sql"
    migration_sql = migration_path.read_text()

    async with db_pool.acquire() as conn:
        await conn.execute(migration_sql)

        # Check indexes
        indexes = await conn.fetch("""
            SELECT indexname
            FROM pg_indexes
            WHERE tablename IN ('sandbox_sessions', 'sandbox_filesystem', 'sandbox_session_bytes')
            AND schemaname = 'public'
        """)

        index_names = {idx["indexname"] for idx in indexes}

        # Primary keys create indexes automatically
        assert "sandbox_sessions_pkey" in index_names
        assert "sandbox_filesystem_pkey" in index_names

        # Our custom indexes
        assert "idx_sandbox_sessions_expires_at" in index_names
        assert "idx_sandbox_sessions_last_accessed" in index_names
        assert "idx_sandbox_filesystem_thread_id" in index_names
        assert "idx_sandbox_filesystem_modified_at" in index_names


async def test_session_bytes_cascade(db_pool):
    """Verify session_bytes cascade deletes with session."""
    # Run migration first
    migration_path = Path(__file__).parent.parent / "migrations" / "001_sandbox_schema.sql"
    migration_sql = migration_path.read_text()

    async with db_pool.acquire() as conn:
        await conn.execute(migration_sql)

        # Create session
        await conn.execute("""
            INSERT INTO sandbox_sessions (thread_id, expires_at)
            VALUES ('test_789', NOW() + INTERVAL '1 day')
        """)

        # Create session bytes
        await conn.execute(
            """
            INSERT INTO sandbox_session_bytes (
                thread_id, session_bytes, session_metadata
            ) VALUES (
                'test_789', $1, '{"test": "data"}'::jsonb
            )
        """,
            b"session data here",
        )

        # Delete session
        await conn.execute("DELETE FROM sandbox_sessions WHERE thread_id = 'test_789'")

        # Verify session_bytes was cascade deleted
        session_bytes = await conn.fetch("""
            SELECT * FROM sandbox_session_bytes WHERE thread_id = 'test_789'
        """)
        assert len(session_bytes) == 0
