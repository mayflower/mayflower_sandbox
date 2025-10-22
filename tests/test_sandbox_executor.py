"""
Tests for the new clean SandboxExecutor architecture.
Tests VFS integration, Pyodide execution, and file sync.
"""

import os
import sys

import asyncpg
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from mayflower_sandbox.sandbox_executor import SandboxExecutor


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
        await conn.execute("""
            INSERT INTO sandbox_sessions (thread_id, expires_at)
            VALUES ('test_sandbox', NOW() + INTERVAL '1 day')
            ON CONFLICT (thread_id) DO NOTHING
        """)

    yield pool
    await pool.close()


@pytest.fixture
async def executor(db_pool):
    """Create executor instance."""
    return SandboxExecutor(db_pool, "test_sandbox", allow_net=True)


@pytest.fixture
async def clean_files(db_pool):
    """Clean files before each test."""
    async with db_pool.acquire() as conn:
        await conn.execute("DELETE FROM sandbox_filesystem WHERE thread_id = 'test_sandbox'")
    yield


async def test_simple_execution(executor, clean_files):
    """Test basic Python execution."""
    result = await executor.execute("print('Hello, World!')")

    assert result.success is True
    assert "Hello, World!" in result.stdout
    assert result.stderr == ""


async def test_computation(executor, clean_files):
    """Test computation with result."""
    code = """
x = 5 + 7
print(f"Result: {x}")
"""
    result = await executor.execute(code)

    assert result.success is True
    assert "Result: 12" in result.stdout


async def test_error_handling(executor, clean_files):
    """Test error is captured."""
    result = await executor.execute("raise ValueError('test error')")

    assert result.success is False
    assert "ValueError" in result.stderr
    assert "test error" in result.stderr


async def test_vfs_preload(executor, db_pool, clean_files):
    """Test files are pre-loaded from VFS."""
    # Put files in VFS first
    async with db_pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO sandbox_filesystem (thread_id, file_path, content, content_type, size)
            VALUES ('test_sandbox', '/tmp/data.txt', $1, 'text/plain', $2)
        """,
            b"Hello from VFS!",
            16,
        )

    # Execute code that reads the file
    code = """
with open('/tmp/data.txt', 'r') as f:
    content = f.read()
    print(f"Read: {content}")
"""

    result = await executor.execute(code)

    assert result.success is True
    assert "Read: Hello from VFS!" in result.stdout


async def test_file_creation_and_save(executor, db_pool, clean_files):
    """Test created files are saved to VFS."""
    code = """
with open('/tmp/output.txt', 'w') as f:
    f.write("Created in Pyodide!")

print("File created")
"""

    result = await executor.execute(code)

    assert result.success is True
    assert "File created" in result.stdout
    assert result.created_files is not None
    assert "/tmp/output.txt" in result.created_files

    # Verify file was saved to PostgreSQL
    async with db_pool.acquire() as conn:
        file_data = await conn.fetchrow("""
            SELECT content FROM sandbox_filesystem
            WHERE thread_id = 'test_sandbox' AND file_path = '/tmp/output.txt'
        """)

        assert file_data is not None
        assert file_data["content"] == b"Created in Pyodide!"


async def test_vfs_roundtrip(executor, db_pool, clean_files):
    """Test full roundtrip: VFS → Pyodide → VFS."""
    # Step 1: Put input file in VFS
    async with db_pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO sandbox_filesystem (thread_id, file_path, content, content_type, size)
            VALUES ('test_sandbox', '/data/input.csv', $1, 'text/csv', $2)
        """,
            b"a,b\n1,2\n3,4",
            11,
        )

    # Step 2: Execute code that reads input and creates output
    code = """
with open('/data/input.csv', 'r') as f:
    lines = f.readlines()
    print(f"Read {len(lines)} lines")

# Process and save
with open('/tmp/output.txt', 'w') as f:
    f.write(f"Processed {len(lines)} lines")
"""

    result = await executor.execute(code)

    assert result.success is True
    assert "Read 3 lines" in result.stdout

    # Step 3: Verify output file was saved to PostgreSQL
    async with db_pool.acquire() as conn:
        output_file = await conn.fetchrow("""
            SELECT content FROM sandbox_filesystem
            WHERE thread_id = 'test_sandbox' AND file_path = '/tmp/output.txt'
        """)

        assert output_file is not None
        assert b"Processed 3 lines" in output_file["content"]


async def test_stateful_execution(db_pool, clean_files):
    """Test stateful session preserves variables."""
    executor = SandboxExecutor(db_pool, "test_sandbox", allow_net=True, stateful=True)

    # First execution: set variable
    result1 = await executor.execute("x = 42")
    assert result1.success is True
    assert result1.session_bytes is not None

    # Second execution: use variable
    result2 = await executor.execute(
        "print(x)",
        session_bytes=result1.session_bytes,
        session_metadata=result1.session_metadata,
    )

    assert result2.success is True
    assert "42" in result2.stdout


async def test_numpy_execution(executor, clean_files):
    """Test numpy works with micropip installation."""
    code = """
import micropip
await micropip.install("numpy")
import numpy as np
arr = np.array([1, 2, 3, 4, 5])
print(f"Sum: {arr.sum()}")
"""
    result = await executor.execute(code)

    assert result.success is True
    assert "Sum: 15" in result.stdout


async def test_multiple_files(executor, db_pool, clean_files):
    """Test creating multiple files."""
    code = """
for i in range(3):
    with open(f'/tmp/file{i}.txt', 'w') as f:
        f.write(f"File {i}")

print("All files created")
"""

    result = await executor.execute(code)

    assert result.success is True
    assert "All files created" in result.stdout
    assert result.created_files is not None
    assert len(result.created_files) >= 3

    # Verify all files are in PostgreSQL
    async with db_pool.acquire() as conn:
        files = await conn.fetch("""
            SELECT file_path FROM sandbox_filesystem
            WHERE thread_id = 'test_sandbox' AND file_path LIKE '/tmp/file%'
            ORDER BY file_path
        """)

        assert len(files) == 3


async def test_matplotlib_auto_backend(executor, clean_files):
    """Test matplotlib works without manual backend configuration."""
    code = """
import micropip
await micropip.install("matplotlib")

# Import matplotlib without setting backend manually
import matplotlib.pyplot as plt
import numpy as np

# Create a simple plot
x = np.linspace(0, 2 * np.pi, 100)
y = np.sin(x)

plt.figure(figsize=(8, 6))
plt.plot(x, y)
plt.title('Sine Wave')
plt.xlabel('x')
plt.ylabel('sin(x)')
plt.grid(True)

# Save the plot
plt.savefig('/tmp/sine_plot.png', dpi=150)
print("Plot created successfully")
"""
    result = await executor.execute(code)

    assert result.success is True, f"Execution failed: {result.stderr}"
    assert "Plot created successfully" in result.stdout
    assert result.created_files is not None
    assert "/tmp/sine_plot.png" in result.created_files
