"""
Tests for session recovery and stateful execution.
"""

import pytest
import asyncpg
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from mayflower_sandbox.session import SessionRecovery, StatefulExecutor


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
async def recovery(db_pool):
    """Create SessionRecovery instance."""
    return SessionRecovery(db_pool)


async def test_save_and_load_session_bytes(recovery, db_pool, clean_db):
    """Test saving and loading session bytes."""
    # Create session first
    async with db_pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO sandbox_sessions (thread_id, expires_at)
            VALUES ('test_thread', NOW() + INTERVAL '1 day')
        """
        )

    # Save session bytes
    test_bytes = b"test session data"
    test_metadata = {"test": "metadata"}

    await recovery.save_session_bytes("test_thread", test_bytes, test_metadata)

    # Load back
    loaded_bytes, loaded_metadata = await recovery.load_session_bytes("test_thread")

    assert loaded_bytes == test_bytes
    assert loaded_metadata == test_metadata


async def test_load_nonexistent_session(recovery, clean_db):
    """Test loading session that doesn't exist."""
    session_bytes, session_metadata = await recovery.load_session_bytes("nonexistent")

    assert session_bytes is None
    assert session_metadata is None


async def test_update_session_bytes(recovery, db_pool, clean_db):
    """Test updating existing session bytes."""
    async with db_pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO sandbox_sessions (thread_id, expires_at)
            VALUES ('test_update', NOW() + INTERVAL '1 day')
        """
        )

    # Save initial
    await recovery.save_session_bytes("test_update", b"version 1", {"version": 1})

    # Update
    await recovery.save_session_bytes("test_update", b"version 2", {"version": 2})

    # Load - should get updated version
    loaded_bytes, loaded_metadata = await recovery.load_session_bytes("test_update")

    assert loaded_bytes == b"version 2"
    assert loaded_metadata["version"] == 2


async def test_delete_session_bytes(recovery, db_pool, clean_db):
    """Test deleting session bytes."""
    async with db_pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO sandbox_sessions (thread_id, expires_at)
            VALUES ('test_delete', NOW() + INTERVAL '1 day')
        """
        )

    # Save session bytes
    await recovery.save_session_bytes("test_delete", b"to delete", {})

    # Delete
    deleted = await recovery.delete_session_bytes("test_delete")
    assert deleted is True

    # Verify gone
    loaded_bytes, _ = await recovery.load_session_bytes("test_delete")
    assert loaded_bytes is None

    # Delete again - should return False
    deleted = await recovery.delete_session_bytes("test_delete")
    assert deleted is False


async def test_stateful_executor_preserves_variables(db_pool, clean_db):
    """Test StatefulExecutor preserves variables across executions."""
    executor = StatefulExecutor(db_pool, "thread_123", allow_net=False)

    # First execution: set variable
    result1 = await executor.execute("x = 42")
    assert result1.success is True

    # Second execution: use variable (should work due to persistence)
    result2 = await executor.execute("print(x)")
    assert result2.success is True
    assert "42" in result2.stdout


async def test_stateful_executor_preserves_imports(db_pool, clean_db):
    """Test imports persist across executions."""
    executor = StatefulExecutor(db_pool, "thread_imports", allow_net=False)

    # First execution: import
    result1 = await executor.execute("import math")
    assert result1.success is True

    # Second execution: use import
    result2 = await executor.execute("print(math.pi)")
    assert result2.success is True
    assert "3.14" in result2.stdout


async def test_stateful_executor_preserves_functions(db_pool, clean_db):
    """Test function definitions persist."""
    executor = StatefulExecutor(db_pool, "thread_funcs", allow_net=False)

    # Define function
    result1 = await executor.execute(
        """
def greet(name):
    return f"Hello, {name}!"
"""
    )
    assert result1.success is True

    # Use function
    result2 = await executor.execute("print(greet('World'))")
    assert result2.success is True
    assert "Hello, World!" in result2.stdout


async def test_stateful_executor_isolates_threads(db_pool, clean_db):
    """Test different threads have isolated state."""
    executor1 = StatefulExecutor(db_pool, "thread_1", allow_net=False)
    executor2 = StatefulExecutor(db_pool, "thread_2", allow_net=False)

    # Thread 1: set variable
    await executor1.execute("x = 100")

    # Thread 2: set different variable
    await executor2.execute("x = 200")

    # Thread 1 should still have x=100
    result1 = await executor2.execute("print(x)")
    assert "100" in result1.stdout

    # Thread 2 should have x=200
    result2 = await executor2.execute("print(x)")
    assert "200" in result2.stdout


async def test_recovery_after_simulated_restart(db_pool, clean_db):
    """Test state recovery after simulated restart."""
    # Create executor and set state
    executor1 = StatefulExecutor(db_pool, "thread_999", allow_net=False)
    await executor1.execute(
        """
data = [1, 2, 3, 4, 5]
sum_value = sum(data)
"""
    )

    # Simulate restart by creating new executor instance
    executor2 = StatefulExecutor(db_pool, "thread_999", allow_net=False)

    # State should be recovered
    result = await executor2.execute("print(f'Sum: {sum_value}')")
    assert result.success is True
    assert "Sum: 15" in result.stdout


async def test_error_doesnt_corrupt_session(db_pool, clean_db):
    """Test that errors don't corrupt session state."""
    executor = StatefulExecutor(db_pool, "thread_error", allow_net=False)

    # Set valid state
    result1 = await executor.execute("x = 100")
    assert result1.success is True

    # Execute code that raises error
    result2 = await executor.execute("raise ValueError('test')")
    assert result2.success is False

    # Previous state should still be intact
    result3 = await executor.execute("print(x)")
    assert result3.success is True
    assert "100" in result3.stdout


async def test_reset_session(db_pool, clean_db):
    """Test resetting session clears state."""
    executor = StatefulExecutor(db_pool, "thread_reset", allow_net=False)

    # Set variable
    await executor.execute("x = 42")

    # Reset session
    await executor.reset_session()

    # Variable should no longer exist
    result = await executor.execute("print(x)")
    assert result.success is False
    assert "NameError" in result.stderr or "not defined" in result.stderr


async def test_session_with_vfs_integration(db_pool, clean_db):
    """Test stateful execution with file operations."""
    executor = StatefulExecutor(db_pool, "thread_vfs", allow_net=False)

    # Create file and store reference
    result1 = await executor.execute(
        """
with open('/tmp/data.txt', 'w') as f:
    f.write('test data')

filename = '/tmp/data.txt'
"""
    )
    assert result1.success is True

    # Use stored filename in next execution
    result2 = await executor.execute(
        """
with open(filename, 'r') as f:
    content = f.read()
print(f'Content: {content}')
"""
    )
    assert result2.success is True
    assert "Content: test data" in result2.stdout


async def test_complex_state_persistence(db_pool, clean_db):
    """Test persistence of complex data structures."""
    executor = StatefulExecutor(db_pool, "thread_complex", allow_net=False)

    # Create complex state
    await executor.execute(
        """
data = {
    'users': [
        {'name': 'Alice', 'age': 30},
        {'name': 'Bob', 'age': 25}
    ],
    'count': 2
}
"""
    )

    # Access complex state
    result = await executor.execute(
        """
print(f"User count: {data['count']}")
print(f"First user: {data['users'][0]['name']}")
"""
    )
    assert result.success is True
    assert "User count: 2" in result.stdout
    assert "First user: Alice" in result.stdout


async def test_stateful_with_allow_net(db_pool, clean_db):
    """Test stateful executor with network access enabled."""
    executor = StatefulExecutor(db_pool, "thread_net", allow_net=True)

    # Should work normally
    result = await executor.execute("x = 42")
    assert result.success is True

    result2 = await executor.execute("print(x)")
    assert result2.success is True
    assert "42" in result2.stdout


async def test_session_metadata_updates(recovery, db_pool, clean_db):
    """Test session metadata is updated."""
    async with db_pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO sandbox_sessions (thread_id, expires_at)
            VALUES ('test_meta', NOW() + INTERVAL '1 day')
        """
        )

    # Save with metadata
    await recovery.save_session_bytes("test_meta", b"data", {"created": "2025-01-01", "version": 1})

    # Update with new metadata
    await recovery.save_session_bytes(
        "test_meta", b"new data", {"created": "2025-01-01", "version": 2, "modified": "2025-01-02"}
    )

    _, metadata = await recovery.load_session_bytes("test_meta")
    assert metadata["version"] == 2
    assert metadata["modified"] == "2025-01-02"


async def test_multiple_sequential_executions(db_pool, clean_db):
    """Test multiple executions in sequence maintain state."""
    executor = StatefulExecutor(db_pool, "thread_seq", allow_net=False)

    # Build up state across multiple executions
    await executor.execute("total = 0")
    await executor.execute("total += 10")
    await executor.execute("total += 20")
    await executor.execute("total += 30")

    # Check final state
    result = await executor.execute("print(f'Total: {total}')")
    assert result.success is True
    assert "Total: 60" in result.stdout
